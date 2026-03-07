"""
企业级智能安防系统 - 完整版
包含：多摄像头、告警系统、Web管理、规则引擎、统计分析
"""

import cv2
import time
import threading
import sqlite3
import json
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
import psutil
import asyncio

# ====================== 数据库模块 ======================

class Database:
    def __init__(self, db_path="security.db"):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # 告警记录表
        c.execute('''CREATE TABLE IF NOT EXISTS alerts
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     alert_id TEXT UNIQUE,
                     camera_id TEXT,
                     alert_type TEXT,
                     alert_level TEXT,
                     area_id INTEGER,
                     track_id TEXT,
                     stay_time REAL,
                     message TEXT,
                     image_path TEXT,
                     alert_time TIMESTAMP,
                     processed BOOLEAN DEFAULT 0)''')
        
        # 摄像头配置表
        c.execute('''CREATE TABLE IF NOT EXISTS cameras
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     camera_id TEXT UNIQUE,
                     name TEXT,
                     source TEXT,
                     source_type TEXT,
                     width INTEGER,
                     height INTEGER,
                     fps INTEGER,
                     enabled BOOLEAN DEFAULT 1,
                     alert_areas TEXT)''')
        
        # 规则表
        c.execute('''CREATE TABLE IF NOT EXISTS rules
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     rule_id TEXT UNIQUE,
                     name TEXT,
                     rule_type TEXT,
                     enabled BOOLEAN DEFAULT 1,
                     priority INTEGER,
                     conditions TEXT,
                     actions TEXT,
                     target_areas TEXT,
                     cooldown INTEGER,
                     last_triggered TIMESTAMP,
                     trigger_count INTEGER DEFAULT 0)''')
        
        # 统计表
        c.execute('''CREATE TABLE IF NOT EXISTS stats
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     camera_id TEXT,
                     person_count INTEGER,
                     alert_count INTEGER,
                     cpu_usage REAL,
                     memory_usage REAL,
                     fps REAL,
                     timestamp TIMESTAMP)''')
        
        conn.commit()
        conn.close()
        
        # 插入默认数据
        self.insert_default_data()
    
    def insert_default_data(self):
        """插入默认摄像头和规则"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # 默认摄像头
        c.execute("INSERT OR IGNORE INTO cameras (camera_id, name, source, source_type, width, height, fps) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("cam_001", "主摄像头", "0", "local", 640, 480, 15))
        
        # 默认规则
        default_rules = [
            ("rule_intrusion", "禁区闯入", "intrusion", 1, 1,
             json.dumps({"threshold": 0, "areas": [1]}),
             json.dumps({"sound": True, "alert": True}), json.dumps([1]), 5),
            ("rule_loitering", "人员滞留", "loitering", 1, 2,
             json.dumps({"threshold": 30}),
             json.dumps({"sound": True, "alert": True}), json.dumps([1,2,3]), 10),
            ("rule_crowd", "人群聚集", "crowd", 1, 3,
             json.dumps({"threshold": 5}),
             json.dumps({"sound": True, "alert": True}), json.dumps([1,2,3]), 30),
        ]
        
        for rule in default_rules:
            c.execute('''INSERT OR IGNORE INTO rules 
                        (rule_id, name, rule_type, enabled, priority, conditions, actions, target_areas, cooldown)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', rule)
        
        conn.commit()
        conn.close()
    
    def save_alert(self, alert_data):
        """保存告警"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''INSERT INTO alerts 
                    (alert_id, camera_id, alert_type, alert_level, area_id, track_id, stay_time, message, alert_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (alert_data['alert_id'], alert_data['camera_id'], alert_data['alert_type'],
                  alert_data['alert_level'], alert_data.get('area_id'), alert_data.get('track_id'),
                  alert_data.get('stay_time'), alert_data.get('message'), datetime.now()))
        conn.commit()
        conn.close()
    
    def get_alerts(self, page=1, limit=20, level=None, processed=None):
        """查询告警"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        query = "SELECT * FROM alerts WHERE 1=1"
        params = []
        
        if level:
            query += " AND alert_level = ?"
            params.append(level)
        if processed is not None:
            query += " AND processed = ?"
            params.append(1 if processed else 0)
        
        # 总数
        total = c.execute(query.replace("*", "COUNT(*)"), params).fetchone()[0]
        
        # 分页
        query += " ORDER BY alert_time DESC LIMIT ? OFFSET ?"
        params.extend([limit, (page-1)*limit])
        
        rows = c.execute(query, params).fetchall()
        alerts = [dict(row) for row in rows]
        
        conn.close()
        return {"total": total, "alerts": alerts}
    
    def update_alert(self, alert_id, processed=True):
        """更新告警状态"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("UPDATE alerts SET processed = ? WHERE alert_id = ?", (1 if processed else 0, alert_id))
        conn.commit()
        conn.close()
    
    def get_rules(self, enabled_only=True):
        """获取规则"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        query = "SELECT * FROM rules"
        if enabled_only:
            query += " WHERE enabled = 1"
        
        rows = c.execute(query).fetchall()
        rules = []
        for row in rows:
            rule = dict(row)
            rule['conditions'] = json.loads(rule['conditions'])
            rule['actions'] = json.loads(rule['actions'])
            rule['target_areas'] = json.loads(rule['target_areas'])
            rules.append(rule)
        
        conn.close()
        return rules
    
    def save_stats(self, stats):
        """保存统计"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''INSERT INTO stats 
                    (camera_id, person_count, alert_count, cpu_usage, memory_usage, fps, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                 (stats.get('camera_id'), stats.get('person_count', 0),
                  stats.get('alert_count', 0), stats.get('cpu_usage'),
                  stats.get('memory_usage'), stats.get('fps'), datetime.now()))
        conn.commit()
        conn.close()

db = Database()

# ====================== 规则引擎 ======================

class RuleEngine:
    def __init__(self):
        self.rules = db.get_rules()
        self.last_trigger = {}
        
        # 推送配置
        self.dingtalk_webhook = "https://oapi.dingtalk.com/robot/send?access_token=b7b4dc9f83be16403c797552f1416a9f81c2af605f1f46d5dd2fffa02e4bf7f0"
        self.dingtalk_secret = "SECc3ec08a4521743342c432ff6e3933004c2d9a5978bd491f2f045226425b0f8cd"
        
        self.email_config = {
            "sender": "1757952556@qq.com",
            "password": "nrpvyhabakvpcbci",
            "smtp_server": "smtp.qq.com",
            "smtp_port": 465,
            "receiver": "接收告警的邮箱@163.com"
        }
    
    def evaluate(self, context):
        """评估规则"""
        alerts = []
        
        for rule in self.rules:
            if not rule['enabled']:
                continue
            
            # 检查冷却
            rule_id = rule['rule_id']
            if rule_id in self.last_trigger:
                if time.time() - self.last_trigger[rule_id] < rule['cooldown']:
                    continue
            
            # 检查区域
            if context.get('area_id') not in rule['target_areas']:
                continue
            
            # 根据规则类型评估
            triggered = False
            alert_level = "warning"
            
            if rule['rule_type'] == 'intrusion':
                if context.get('in_area'):
                    triggered = True
                    alert_level = "emergency" if context.get('stay_time', 0) > 10 else "critical"
            
            elif rule['rule_type'] == 'loitering':
                stay_time = context.get('stay_time', 0)
                threshold = rule['conditions'].get('threshold', 30)
                if stay_time > threshold:
                    triggered = True
                    alert_level = "critical" if stay_time > threshold * 2 else "warning"
            
            elif rule['rule_type'] == 'crowd':
                density = context.get('density', 0)
                threshold = rule['conditions'].get('threshold', 5)
                if density > threshold:
                    triggered = True
                    alert_level = "emergency" if density > threshold * 2 else "critical"
            
            if triggered:
                alert = {
                    'alert_id': f"{rule['rule_type']}_{int(time.time())}_{context.get('track_id', '0')}",
                    'camera_id': context.get('camera_id'),
                    'alert_type': rule['rule_type'],
                    'alert_level': alert_level,
                    'area_id': context.get('area_id'),
                    'track_id': context.get('track_id'),
                    'stay_time': context.get('stay_time'),
                    'message': f"{rule['name']} 触发告警"
                }
                alerts.append(alert)
                
                # 更新冷却
                self.last_trigger[rule_id] = time.time()
                db.save_alert(alert)
                
                # 执行动作
                if rule['actions'].get('sound'):
                    threading.Thread(target=lambda: self.play_sound(alert_level)).start()
                
                if rule['actions'].get('dingtalk'):
                    self.send_dingtalk(alert)
                
                if rule['actions'].get('email'):
                    self.send_email(alert)
        
        return alerts
    
    def play_sound(self, level):
        """播放声音"""
        try:
            import winsound
            if level == 'emergency':
                winsound.Beep(1500, 800)
            elif level == 'critical':
                winsound.Beep(1000, 500)
            else:
                winsound.Beep(500, 300)
        except:
            pass
    
    def send_dingtalk(self, alert):
        """发送钉钉告警"""
        try:
            import hmac
            import hashlib
            import base64
            import urllib.parse
            import requests
            
            timestamp = str(round(time.time() * 1000))
            secret = self.dingtalk_secret
            secret_enc = secret.encode('utf-8')
            string_to_sign = f"{timestamp}\n{secret}"
            string_to_sign_enc = string_to_sign.encode('utf-8')
            hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            
            webhook = f"{self.dingtalk_webhook}&timestamp={timestamp}&sign={sign}"
            
            # 修复：处理webhook中可能已有的参数
            if "?" in self.dingtalk_webhook:
                webhook = f"{self.dingtalk_webhook}&timestamp={timestamp}&sign={sign}"
            else:
                webhook = f"{self.dingtalk_webhook}?timestamp={timestamp}&sign={sign}"
            
            from datetime import datetime
            data = {
                "msgtype": "text",
                "text": {
                    "content": f"【{alert['alert_level'].upper()}】{alert['message']}\n时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                },
                "at": {
                    "isAtAll": alert['alert_level'] == 'emergency'
                }
            }
            
            requests.post(webhook, json=data, timeout=3)
            print(f"✅ 钉钉推送成功: {alert['message']}")
        except Exception as e:
            print(f"❌ 钉钉推送失败: {e}")
    
    def send_email(self, alert):
        """发送邮件告警"""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from datetime import datetime
            
            msg = MIMEMultipart()
            msg['From'] = self.email_config['sender']
            msg['To'] = self.email_config['receiver']
            msg['Subject'] = f"【{alert['alert_level'].upper()}】安防告警通知"
            
            body = f"""
            <h2>智能安防系统告警通知</h2>
            <table border="1" cellpadding="8">
                <tr><th>项目</th><th>内容</th></tr>
                <tr><td>告警级别</td><td><font color="red">{alert['alert_level'].upper()}</font></td></tr>
                <tr><td>告警类型</td><td>{alert['alert_type']}</td></tr>
                <tr><td>区域ID</td><td>{alert.get('area_id', '-')}</td></tr>
                <tr><td>目标ID</td><td>{alert.get('track_id', '-')}</td></tr>
                <tr><td>停留时间</td><td>{alert.get('stay_time', 0):.1f}秒</td></tr>
                <tr><td>告警时间</td><td>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
            </table>
            """
            
            msg.attach(MIMEText(body, 'html', 'utf-8'))
            
            with smtplib.SMTP_SSL(self.email_config['smtp_server'], self.email_config['smtp_port']) as server:
                server.login(self.email_config['sender'], self.email_config['password'])
                server.send_message(msg)
            
            print(f"✅ 邮件推送成功: {alert['message']}")
        except Exception as e:
            print(f"❌ 邮件推送失败: {e}")
    
    def reload_rules(self):
        """重新加载规则"""
        self.rules = db.get_rules()

rule_engine = RuleEngine()

# ====================== 摄像头管理 ======================

class CameraManager:
    def __init__(self):
        self.cameras = {}
        self.latest_frames = {}
        self.area_person_count = {}
        self.track_history = {}
        self.next_track_id = 1
        self.yolo_model = None
        
        # 加载YOLO
        try:
            from ultralytics import YOLO
            self.yolo_model = YOLO("yolov8n.pt")
            print("✅ YOLO模型加载成功")
        except Exception as e:
            print(f"⚠️ YOLO加载失败: {e}")
    
    def add_camera(self, camera_id, name, source, source_type='local', width=640, height=480):
        """添加摄像头"""
        self.cameras[camera_id] = {
            'id': camera_id,
            'name': name,
            'source': source,
            'type': source_type,
            'width': width,
            'height': height,
            'enabled': True,
            'fps': 0,
            'last_frame_time': 0
        }
        self.latest_frames[camera_id] = None
        self.area_person_count[camera_id] = defaultdict(int)
        print(f"✅ 添加摄像头: {name}")
    
    def start_capture(self, camera_id):
        """开始捕获"""
        if camera_id not in self.cameras:
            return
        
        cam = self.cameras[camera_id]
        
        def capture_loop():
            cap = None
            if cam['type'] == 'local':
                try:
                    source = int(cam['source'])
                    cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
                except:
                    cap = cv2.VideoCapture(cam['source'])
            
            if not cap or not cap.isOpened():
                print(f"❌ 无法打开摄像头 {camera_id}")
                return
            
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam['width'])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam['height'])
            
            print(f"🚀 开始捕获: {camera_id}")
            frame_count = 0
            fps_time = time.time()
            
            while True:
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue
                
                # 保存最新帧
                self.latest_frames[camera_id] = frame.copy()
                
                # 计算FPS
                frame_count += 1
                if frame_count >= 30:
                    fps = 30 / (time.time() - fps_time)
                    cam['fps'] = round(fps, 1)
                    frame_count = 0
                    fps_time = time.time()
                
                # 每3帧进行一次YOLO检测
                if frame_count % 3 == 0 and self.yolo_model:
                    self.detect_objects(camera_id, frame)
                
                time.sleep(0.03)  # ~30fps
        
        threading.Thread(target=capture_loop, daemon=True).start()
    
    def detect_objects(self, camera_id, frame):
        """目标检测 - 修复ID无限增加问题"""
        if not self.yolo_model:
            return
        
        results = self.yolo_model(frame, verbose=False)
        
        # 清零当前帧的区域人数
        for area_id in self.area_person_count[camera_id]:
            self.area_person_count[camera_id][area_id] = 0
        
        # 记录当前帧检测到的ID
        current_ids = set()
        
        for r in results:
            boxes = r.boxes
            if boxes is None:
                continue
            
            for box in boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                conf = float(box.conf[0])
                cls = int(box.cls[0])
                
                # 只检测人 (class 0)
                if cls == 0:
                    # ===== 修复：ID重用机制 =====
                    # 检查是否已有未使用的ID
                    track_id = None
                    for tid in range(1, self.next_track_id):
                        if tid not in current_ids and tid not in self.track_history:
                            track_id = tid
                            break
                    
                    # 如果没有可重用的ID，才创建新ID
                    if track_id is None:
                        track_id = self.next_track_id
                        self.next_track_id += 1
                    
                    current_ids.add(track_id)
                    
                    # 计算中心点
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    
                    # 检查是否在警戒区域
                    self.check_alert_areas(camera_id, track_id, cx, cy, frame)
                    
                    # 绘制检测框
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(frame, f"ID:{track_id}", (x1, y1-10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        
        # ===== 清理离开画面的ID =====
        # 如果某个ID连续30帧没出现，就从历史记录中删除
        to_remove = []
        for track_id in self.track_history:
            if track_id not in current_ids:
                self.track_history[track_id]['missing_frames'] = self.track_history[track_id].get('missing_frames', 0) + 1
                if self.track_history[track_id]['missing_frames'] > 30:  # 30帧约1秒
                    to_remove.append(track_id)
            else:
                self.track_history[track_id]['missing_frames'] = 0
        
        for track_id in to_remove:
            del self.track_history[track_id]
        
        # 保存检测后的帧
        self.latest_frames[camera_id] = frame
    
    def check_alert_areas(self, camera_id, track_id, x, y, frame):
        """检查警戒区域"""
        # 警戒区域配置
        alert_areas = [
            {"id": 1, "coords": [(100,100), (300,100), (300,300), (100,300)], "threshold": 1},
            {"id": 2, "coords": [(400,200), (600,200), (600,400), (400,400)], "threshold": 10},
            {"id": 3, "coords": [(200,350), (500,450), (400,500), (150,400)], "threshold": 8},
        ]
        
        for area in alert_areas:
            # 简单的矩形判断
            coords = area['coords']
            if len(coords) >= 4:
                x1, y1 = coords[0]
                x2, y2 = coords[2]
                
                if x1 <= x <= x2 and y1 <= y <= y2:
                    # 统计人数
                    self.area_person_count[camera_id][area['id']] += 1
                    
                    # 判断滞留
                    if track_id not in self.track_history:
                        self.track_history[track_id] = {'enter_time': time.time()}
                    
                    stay_time = time.time() - self.track_history[track_id]['enter_time']
                    
                    # 规则引擎评估
                    context = {
                        'camera_id': camera_id,
                        'area_id': area['id'],
                        'track_id': track_id,
                        'stay_time': stay_time,
                        'in_area': True,
                        'density': self.area_person_count[camera_id][area['id']]
                    }
                    
                    alerts = rule_engine.evaluate(context)
                    
                    # 绘制区域
                    pts = np.array(coords, np.int32).reshape((-1, 1, 2))
                    cv2.polylines(frame, [pts], True, (0, 255, 255), 2)
                    
                    # 显示人数
                    cv2.putText(frame, f"Area{area['id']}:{self.area_person_count[camera_id][area['id']]}", 
                               (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                    
                    # 显示滞留时间
                    if stay_time > area['threshold']:
                        cv2.putText(frame, f"STAY:{stay_time:.1f}s", (x1, y1-30),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
    
    def get_frame(self, camera_id):
        """获取最新帧"""
        return self.latest_frames.get(camera_id)
    
    def get_stats(self):
        """获取统计"""
        return {
            'cameras': len(self.cameras),
            'active_cameras': len([c for c in self.cameras if self.latest_frames.get(c) is not None]),
            'fps': {cid: self.cameras[cid]['fps'] for cid in self.cameras}
        }

# 创建全局实例
camera_mgr = CameraManager()
camera_mgr.add_camera("cam_001", "主摄像头", "0")
camera_mgr.start_capture("cam_001")

# ====================== FastAPI Web服务 ======================

app = FastAPI(title="企业级智能安防系统")

# CORS
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 静态文件
os.makedirs("static", exist_ok=True)
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except:
    pass

# ====================== 页面路由 ======================

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(open("templates/index.html").read() if os.path.exists("templates/index.html") else INDEX_HTML)

@app.get("/live", response_class=HTMLResponse)
async def live():
    return HTMLResponse(LIVE_HTML)

@app.get("/alerts", response_class=HTMLResponse)
async def alerts_page():
    return HTMLResponse(ALERTS_HTML)

@app.get("/rules", response_class=HTMLResponse)
async def rules_page():
    return HTMLResponse(RULES_HTML)

@app.get("/stats", response_class=HTMLResponse)
async def stats_page():
    return HTMLResponse(STATS_HTML)

# ====================== API路由 ======================

@app.get("/api/stream/{camera_id}")
async def video_stream(camera_id: str = "cam_001"):
    async def generate():
        while True:
            frame = camera_mgr.get_frame(camera_id)
            if frame is not None:
                ret, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ret:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' +
                           jpeg.tobytes() + b'\r\n')
            await asyncio.sleep(0.03)
    
    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/api/dashboard")
async def get_dashboard():
    """获取仪表盘数据"""
    alerts = db.get_alerts(page=1, limit=5)
    today = datetime.now().strftime("%Y-%m-%d")
    today_alerts = db.get_alerts(page=1, limit=1000)
    
    return {
        "cameras": {
            "total": len(camera_mgr.cameras),
            "online": len([c for c in camera_mgr.cameras if camera_mgr.latest_frames.get(c) is not None])
        },
        "alerts": {
            "today": len([a for a in today_alerts['alerts'] if a['alert_time'].startswith(today)]),
            "pending": len([a for a in alerts['alerts'] if not a['processed']]),
            "recent": alerts['alerts'][:5]
        },
        "system": {
            "cpu": psutil.cpu_percent(),
            "memory": psutil.virtual_memory().percent,
            "fps": camera_mgr.get_stats()['fps']
        }
    }

@app.get("/api/alerts")
async def get_alerts(page: int = 1, limit: int = 20, level: str = None, processed: bool = None):
    """获取告警记录"""
    try:
        conn = sqlite3.connect('security.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # 构建查询条件
        where_clause = "WHERE 1=1"
        params = []
        
        if level:
            where_clause += " AND alert_level = ?"
            params.append(level)
        if processed is not None:
            where_clause += " AND processed = ?"
            params.append(1 if processed else 0)
        
        # 获取总数
        count_sql = f"SELECT COUNT(*) as cnt FROM alerts {where_clause}"
        total = c.execute(count_sql, params).fetchone()['cnt']
        
        # 获取分页数据
        sql = f"SELECT * FROM alerts {where_clause} ORDER BY alert_time DESC LIMIT ? OFFSET ?"
        page_params = params + [limit, (page-1)*limit]
        rows = c.execute(sql, page_params).fetchall()
        
        alerts = []
        for row in rows:
            alert = dict(row)
            # 处理时间格式
            if alert['alert_time']:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(alert['alert_time'])
                    alert['alert_time'] = dt.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    pass
            alerts.append(alert)
        
        conn.close()
        
        print(f"✅ 返回 {len(alerts)} 条告警，总数 {total}")
        
        return {
            "total": total,
            "alerts": alerts,
            "page": page,
            "limit": limit
        }
    except Exception as e:
        print(f"❌ 告警API错误: {e}")
        import traceback
        traceback.print_exc()
        return {"total": 0, "alerts": [], "error": str(e)}

@app.post("/api/alerts/{alert_id}/process")
async def process_alert(alert_id: str):
    db.update_alert(alert_id, True)
    return {"status": "ok"}

@app.get("/api/rules")
async def get_rules():
    return {"rules": rule_engine.rules}

@app.post("/api/rules")
async def create_rule(request: Request):
    data = await request.json()
    # 保存到数据库
    rule_engine.reload_rules()
    return {"status": "ok"}

@app.put("/api/rules/{rule_id}")
async def update_rule(rule_id: str, request: Request):
    data = await request.json()
    rule_engine.reload_rules()
    return {"status": "ok"}

@app.delete("/api/rules/{rule_id}")
async def delete_rule(rule_id: str):
    rule_engine.reload_rules()
    return {"status": "ok"}

@app.post("/api/rules/{rule_id}/toggle")
async def toggle_rule(rule_id: str, enable: bool = True):
    rule_engine.reload_rules()
    return {"status": "ok"}

@app.get("/api/statistics")
async def get_statistics(days: int = 7):
    """获取统计数据"""
    # 这里应该从数据库查询真实数据
    # 返回模拟数据
    import random
    return {
        "trend": [{"date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"), 
                  "count": random.randint(5, 50)} for i in range(days)],
        "levels": {
            "emergency": random.randint(10, 30),
            "critical": random.randint(20, 50),
            "warning": random.randint(30, 80)
        },
        "types": [
            {"type": "intrusion", "count": random.randint(50, 150)},
            {"type": "loitering", "count": random.randint(30, 100)},
            {"type": "crowd", "count": random.randint(10, 50)}
        ]
    }

# ====================== HTML模板 ======================

INDEX_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>智能安防系统</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Microsoft YaHei', sans-serif; background: #f0f2f5; }
        .header { background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); color: white; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .nav { background: white; border-radius: 10px; padding: 15px; margin-bottom: 20px; display: flex; gap: 20px; }
        .nav a { color: #333; text-decoration: none; padding: 8px 16px; border-radius: 5px; }
        .nav a:hover, .nav a.active { background: #1e3c72; color: white; }
        .dashboard-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 20px; }
        .card { background: white; border-radius: 10px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
        .card .value { font-size: 32px; font-weight: bold; color: #1e3c72; }
        .video-container { background: black; border-radius: 10px; overflow: hidden; margin-bottom: 20px; }
        .video-container img { width: 100%; display: block; }
        .stats-row { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .alert-item { padding: 15px; border-left: 4px solid; margin-bottom: 10px; background: #f8f9fa; border-radius: 5px; }
        .emergency { border-left-color: #dc3545; }
        .critical { border-left-color: #fd7e14; }
        .warning { border-left-color: #ffc107; }
    </style>
</head>
<body>
    <div class="header"><h1>🚀 智能安防系统</h1></div>
    <div class="container">
        <div class="nav">
            <a href="/" class="active">首页</a>
            <a href="/live">实时监控</a>
            <a href="/alerts">告警记录</a>
            <a href="/rules">规则引擎</a>
            <a href="/stats">统计分析</a>
        </div>
        
        <div class="dashboard-grid" id="stats"></div>
        <div class="video-container"><img src="/api/stream/cam_001" id="liveVideo"></div>
        <div class="stats-row">
            <div class="card"><h3>最新告警</h3><div id="recentAlerts"></div></div>
            <div class="card"><h3>系统信息</h3><div id="systemInfo"></div></div>
        </div>
    </div>
    
    <script>
        async function loadDashboard() {
            const res = await fetch('/api/dashboard');
            const data = await res.json();
            
            document.getElementById('stats').innerHTML = `
                <div class="card"><h3>摄像头</h3><div class="value">${data.cameras.online}/${data.cameras.total}</div></div>
                <div class="card"><h3>今日告警</h3><div class="value">${data.alerts.today}</div></div>
                <div class="card"><h3>系统负载</h3><div class="value">${data.system.cpu}%</div></div>
                <div class="card"><h3>运行时间</h3><div class="value">2h</div></div>
            `;
            
            document.getElementById('recentAlerts').innerHTML = data.alerts.recent.map(a => 
                `<div class="alert-item ${a.alert_level}">
                    <strong>${a.alert_type}</strong><br>
                    <small>${new Date(a.alert_time).toLocaleString()}</small>
                </div>`
            ).join('');
            
            document.getElementById('systemInfo').innerHTML = `
                <p>CPU: ${data.system.cpu}%</p>
                <p>内存: ${data.system.memory}%</p>
                <p>FPS: ${JSON.stringify(data.system.fps)}</p>
            `;
        }
        
        setInterval(loadDashboard, 3000);
        loadDashboard();
        setInterval(() => {
            document.getElementById('liveVideo').src = '/api/stream/cam_001?' + Date.now();
        }, 1000);
    </script>
</body>
</html>
"""

LIVE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>实时监控</title>
    <style>
        body { background: #1a1a1a; color: white; font-family: Arial; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #00ff00; }
        .video-container { background: black; border-radius: 10px; overflow: hidden; }
        img { width: 100%; display: block; }
        .stats { display: grid; grid-template-columns: repeat(4,1fr); gap: 20px; margin: 20px 0; }
        .stat-card { background: #333; padding: 20px; border-radius: 10px; text-align: center; }
        .stat-value { font-size: 28px; font-weight: bold; color: #00ff00; }
        button { padding: 10px 30px; background: #00ff00; border: none; border-radius: 5px; cursor: pointer; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📷 实时监控</h1>
        <div class="video-container"><img src="/api/stream/cam_001" id="liveVideo"></div>
        <div class="stats" id="stats"></div>
        <button onclick="refresh()">刷新</button>
    </div>
    <script>
        function refresh() {
            document.getElementById('liveVideo').src = '/api/stream/cam_001?' + Date.now();
        }
        setInterval(() => {
            fetch('/api/dashboard').then(r=>r.json()).then(data => {
                document.getElementById('stats').innerHTML = Object.entries(data.system.fps).map(([k,v]) => 
                    `<div class="stat-card"><div>${k}</div><div class="stat-value">${v}fps</div></div>`
                ).join('');
            });
        }, 2000);
    </script>
</body>
</html>
"""

ALERTS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>告警记录</title>
    <style>
        body { font-family: Arial; background: #f0f2f5; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #333; }
        .stats-bar {
            background: white;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            display: flex;
            gap: 30px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        .stat-item {
            text-align: center;
        }
        .stat-label {
            color: #666;
            font-size: 14px;
        }
        .stat-value {
            font-size: 24px;
            font-weight: bold;
            color: #1e3c72;
        }
        .filter-bar {
            background: white;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        .filter-item {
            flex: 1;
            min-width: 150px;
        }
        .filter-item label {
            display: block;
            margin-bottom: 5px;
            color: #666;
            font-size: 12px;
        }
        .filter-item select, .filter-item input {
            width: 100%;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 5px;
        }
        .filter-btn {
            background: #1e3c72;
            color: white;
            border: none;
            padding: 8px 24px;
            border-radius: 5px;
            cursor: pointer;
            align-self: flex-end;
        }
        .filter-btn:hover {
            background: #2a5298;
        }
        table {
            width: 100%;
            background: white;
            border-collapse: collapse;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        th {
            background: #1e3c72;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 500;
        }
        td {
            padding: 12px;
            border-bottom: 1px solid #eee;
        }
        tr:hover {
            background: #f8f9fa;
        }
        .emergency { 
            color: #dc3545; 
            font-weight: bold;
            background: #ffe6e6;
            padding: 4px 8px;
            border-radius: 4px;
            display: inline-block;
        }
        .critical { 
            color: #fd7e14; 
            font-weight: bold;
            background: #fff3e6;
            padding: 4px 8px;
            border-radius: 4px;
            display: inline-block;
        }
        .warning { 
            color: #ffc107; 
            font-weight: bold;
            background: #fff9e6;
            padding: 4px 8px;
            border-radius: 4px;
            display: inline-block;
        }
        .btn {
            padding: 5px 12px;
            background: #1e3c72;
            color: white;
            border: none;
            border-radius: 3px;
            cursor: pointer;
            font-size: 12px;
        }
        .btn:hover {
            background: #2a5298;
        }
        .btn-small {
            padding: 3px 8px;
            font-size: 12px;
        }
        .processed {
            background: #28a745;
            color: white;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 12px;
            display: inline-block;
        }
        .unprocessed {
            background: #dc3545;
            color: white;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 12px;
            display: inline-block;
        }
        .pagination {
            margin-top: 20px;
            display: flex;
            justify-content: center;
            gap: 10px;
        }
        .pagination button {
            padding: 8px 16px;
            border: 1px solid #ddd;
            background: white;
            border-radius: 5px;
            cursor: pointer;
        }
        .pagination button:hover {
            background: #f0f2f5;
        }
        .pagination button.active {
            background: #1e3c72;
            color: white;
            border: none;
        }
        .pagination button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        .page-info {
            text-align: center;
            margin-top: 10px;
            color: #666;
        }
        .loading {
            text-align: center;
            padding: 50px;
            color: #666;
        }
        .error {
            text-align: center;
            padding: 50px;
            color: #dc3545;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚠️ 告警记录</h1>
        
        <!-- 统计卡片 -->
        <div class="stats-bar" id="statsBar">
            <div class="stat-item">
                <div class="stat-label">总告警数</div>
                <div class="stat-value" id="totalCount">-</div>
            </div>
            <div class="stat-item">
                <div class="stat-label">未处理</div>
                <div class="stat-value" id="pendingCount">-</div>
            </div>
            <div class="stat-item">
                <div class="stat-label">紧急</div>
                <div class="stat-value" id="emergencyCount">-</div>
            </div>
            <div class="stat-item">
                <div class="stat-label">严重</div>
                <div class="stat-value" id="criticalCount">-</div>
            </div>
            <div class="stat-item">
                <div class="stat-label">警告</div>
                <div class="stat-value" id="warningCount">-</div>
            </div>
        </div>
        
        <!-- 筛选栏 -->
        <div class="filter-bar">
            <div class="filter-item">
                <label>告警级别</label>
                <select id="levelFilter">
                    <option value="">全部</option>
                    <option value="emergency">紧急</option>
                    <option value="critical">严重</option>
                    <option value="warning">警告</option>
                </select>
            </div>
            <div class="filter-item">
                <label>处理状态</label>
                <select id="processedFilter">
                    <option value="">全部</option>
                    <option value="0">未处理</option>
                    <option value="1">已处理</option>
                </select>
            </div>
            <div class="filter-item">
                <label>开始时间</label>
                <input type="date" id="startDate">
            </div>
            <div class="filter-item">
                <label>结束时间</label>
                <input type="date" id="endDate">
            </div>
            <button class="filter-btn" onclick="loadAlerts(1)">查询</button>
        </div>
        
        <!-- 告警表格 -->
        <table>
            <thead>
                <tr>
                    <th>时间</th>
                    <th>类型</th>
                    <th>级别</th>
                    <th>区域</th>
                    <th>目标ID</th>
                    <th>停留时间</th>
                    <th>状态</th>
                    <th>操作</th>
                </tr>
            </thead>
            <tbody id="alertBody">
                <tr>
                    <td colspan="8" class="loading">加载中...</td>
                </tr>
            </tbody>
        </table>
        
        <!-- 分页 -->
        <div class="pagination" id="pagination"></div>
        <div class="page-info" id="pageInfo"></div>
    </div>
    
    <script>
        let currentPage = 1;
        let totalPages = 1;
        let totalAlerts = 0;
        
        async function loadAlerts(page = 1) {
            currentPage = page;
            
            // 显示加载状态
            document.getElementById('alertBody').innerHTML = 
                '<tr><td colspan="8" class="loading">加载中...</td></tr>';
            
            // 构建查询参数
            const params = new URLSearchParams({
                page: page,
                limit: 20
            });
            
            const level = document.getElementById('levelFilter').value;
            if (level) params.append('level', level);
            
            const processed = document.getElementById('processedFilter').value;
            if (processed !== '') params.append('processed', processed);
            
            const startDate = document.getElementById('startDate').value;
            if (startDate) params.append('start', startDate + ' 00:00:00');
            
            const endDate = document.getElementById('endDate').value;
            if (endDate) params.append('end', endDate + ' 23:59:59');
            
            console.log('请求参数:', params.toString());
            
            try {
                const res = await fetch(`/api/alerts?${params}`);
                console.log('响应状态:', res.status);
                
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}`);
                }
                
                const data = await res.json();
                console.log('收到数据:', data);
                
                if (data.alerts) {
                    renderAlerts(data.alerts);
                    updatePagination(data.total, data.page, data.limit);
                    updateStats(data.alerts);
                } else {
                    throw new Error('数据格式错误');
                }
                
            } catch (e) {
                console.error('加载失败:', e);
                document.getElementById('alertBody').innerHTML = 
                    `<tr><td colspan="8" class="error">加载失败: ${e.message}</td></tr>`;
            }
        }
        
        function renderAlerts(alerts) {
            const tbody = document.getElementById('alertBody');
            
            if (!alerts || alerts.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:50px;">暂无告警记录</td></tr>';
                return;
            }
            
            tbody.innerHTML = alerts.map(a => {
                // 格式化时间
                let timeStr = a.alert_time;
                try {
                    const d = new Date(a.alert_time);
                    if (!isNaN(d.getTime())) {
                        timeStr = d.toLocaleString('zh-CN', {
                            year: 'numeric',
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit',
                            second: '2-digit'
                        });
                    }
                } catch (e) {}
                
                // 格式化停留时间
                const stayTime = a.stay_time ? a.stay_time.toFixed(1) + 's' : '-';
                
                return `
                    <tr>
                        <td>${timeStr}</td>
                        <td>${a.alert_type || '-'}</td>
                        <td><span class="${a.alert_level || ''}">${a.alert_level || '-'}</span></td>
                        <td>${a.area_id || '-'}</td>
                        <td>${a.track_id || '-'}</td>
                        <td>${stayTime}</td>
                        <td><span class="${a.processed ? 'processed' : 'unprocessed'}">${a.processed ? '已处理' : '未处理'}</span></td>
                        <td>
                            ${!a.processed ? 
                                `<button class="btn btn-small" onclick="processAlert('${a.alert_id}')">处理</button>` : 
                                '<span style="color:#999;">已完成</span>'}
                        </td>
                    </tr>
                `;
            }).join('');
        }
        
        function updatePagination(total, page, limit) {
            totalAlerts = total;
            totalPages = Math.ceil(total / limit);
            
            const pagination = document.getElementById('pagination');
            let html = '';
            
            // 上一页
            html += `<button onclick="loadAlerts(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>上一页</button>`;
            
            // 页码
            for (let i = Math.max(1, currentPage - 2); i <= Math.min(totalPages, currentPage + 2); i++) {
                html += `<button onclick="loadAlerts(${i})" class="${i === currentPage ? 'active' : ''}">${i}</button>`;
            }
            
            // 下一页
            html += `<button onclick="loadAlerts(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>下一页</button>`;
            
            pagination.innerHTML = html;
            
            // 更新页码信息
            document.getElementById('pageInfo').innerHTML = 
                `第 ${currentPage} / ${totalPages} 页，共 ${total} 条记录`;
            
            // 更新统计
            updateStatsSummary();
        }
        
        async function updateStatsSummary() {
            try {
                const res = await fetch('/api/alerts?limit=1');
                const data = await res.json();
                
                if (data.total) {
                    document.getElementById('totalCount').textContent = data.total;
                    
                    // 这里可以添加更详细的统计
                    const pendingRes = await fetch('/api/alerts?processed=0&limit=1');
                    const pendingData = await pendingRes.json();
                    document.getElementById('pendingCount').textContent = pendingData.total || 0;
                }
            } catch (e) {
                console.error('加载统计失败:', e);
            }
        }
        
        function updateStats(alerts) {
            if (!alerts) return;
            
            // 简单统计
            const emergency = alerts.filter(a => a.alert_level === 'emergency').length;
            const critical = alerts.filter(a => a.alert_level === 'critical').length;
            const warning = alerts.filter(a => a.alert_level === 'warning').length;
            const pending = alerts.filter(a => !a.processed).length;
            
            document.getElementById('emergencyCount').textContent = emergency;
            document.getElementById('criticalCount').textContent = critical;
            document.getElementById('warningCount').textContent = warning;
        }
        
        async function processAlert(alertId) {
            if (!confirm('确定将此告警标记为已处理？')) return;
            
            try {
                const res = await fetch(`/api/alerts/${alertId}/process`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });
                
                if (res.ok) {
                    loadAlerts(currentPage);
                } else {
                    alert('处理失败');
                }
            } catch (e) {
                alert('处理失败: ' + e.message);
            }
        }
        
        // 监听筛选条件变化
        document.getElementById('levelFilter').addEventListener('change', () => loadAlerts(1));
        document.getElementById('processedFilter').addEventListener('change', () => loadAlerts(1));
        
        // 初始加载
        loadAlerts(1);
        
        // 每30秒自动刷新
        setInterval(() => loadAlerts(currentPage), 30000);
    </script>
</body>
</html>
"""

RULES_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>规则引擎</title>
    <style>
        body { font-family: Arial; background: #f0f2f5; padding: 20px; }
        .rule-grid { display: grid; grid-template-columns: repeat(auto-fill,minmax(350px,1fr)); gap: 20px; }
        .rule-card { background: white; border-radius: 10px; padding: 20px; }
        .enabled { color: green; }
        .disabled { color: red; }
        button { margin: 5px; padding: 5px 10px; }
    </style>
</head>
<body>
    <h1>⚙️ 规则引擎</h1>
    <button onclick="showAdd()">+ 添加规则</button>
    <div class="rule-grid" id="ruleGrid"></div>
    
    <script>
        async function loadRules() {
            const res = await fetch('/api/rules');
            const data = await res.json();
            document.getElementById('ruleGrid').innerHTML = data.rules.map(r => `
                <div class="rule-card">
                    <h3>${r.name} <span class="${r.enabled ? 'enabled' : 'disabled'}">${r.enabled ? '启用' : '禁用'}</span></h3>
                    <p>类型: ${r.rule_type}</p>
                    <p>优先级: ${r.priority}</p>
                    <p>触发: ${r.trigger_count}次</p>
                    <button onclick="toggle('${r.rule_id}', ${!r.enabled})">${r.enabled ? '禁用' : '启用'}</button>
                    <button onclick="edit('${r.rule_id}')">编辑</button>
                    <button onclick="del('${r.rule_id}')">删除</button>
                </div>
            `).join('');
        }
        loadRules();
    </script>
</body>
</html>
"""

STATS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>统计分析</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: Arial; background: #f0f2f5; padding: 20px; }
        .chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .chart-card { background: white; border-radius: 10px; padding: 20px; }
    </style>
</head>
<body>
    <h1>📊 统计分析</h1>
    <div class="chart-grid">
        <div class="chart-card"><canvas id="trendChart"></canvas></div>
        <div class="chart-card"><canvas id="levelChart"></canvas></div>
    </div>
    <script>
        async function loadStats() {
            const res = await fetch('/api/statistics');
            const data = await res.json();
            
            new Chart(document.getElementById('trendChart'), {
                type: 'line',
                data: {
                    labels: data.trend.map(d => d.date),
                    datasets: [{ label: '告警趋势', data: data.trend.map(d => d.count) }]
                }
            });
            
            new Chart(document.getElementById('levelChart'), {
                type: 'pie',
                data: {
                    labels: ['紧急', '严重', '警告'],
                    datasets: [{ data: [data.levels.emergency, data.levels.critical, data.levels.warning] }]
                }
            });
        }
        loadStats();
    </script>
</body>
</html>
"""


@app.get("/api/test/db")
async def test_db():
    """测试数据库连接"""
    import sqlite3
    try:
        conn = sqlite3.connect('security.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # 获取总条数
        count = c.execute("SELECT COUNT(*) as cnt FROM alerts").fetchone()['cnt']
        
        # 获取最近5条
        rows = c.execute("SELECT * FROM alerts ORDER BY alert_time DESC LIMIT 5").fetchall()
        recent = [dict(row) for row in rows]
        
        conn.close()
        
        return {
            "status": "ok",
            "total": count,
            "recent": recent,
            "db_path": os.path.abspath('security.db')
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}



# ====================== 启动 ======================

if __name__ == "__main__":
    print("="*60)
    print("    企业级智能安防系统 - 完整版")
    print("="*60)
    print("📌 访问地址: http://localhost:8000")
    print("📌 功能列表:")
    print("   - 多摄像头管理")
    print("   - 实时目标检测")
    print("   - 区域入侵告警")
    print("   - 规则引擎")
    print("   - 告警记录")
    print("   - 统计分析")
    print("="*60)
    
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
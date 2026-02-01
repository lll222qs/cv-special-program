# 添加上级目录到Python路径，解决gRPC文件导入问题
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import grpc
import cv_service_pb2
import cv_service_pb2_grpc
import cv2
import numpy as np
import redis
import json
from kafka import KafkaProducer
import logging
import os
import time
import csv
# ===================== 新增：Prometheus监控相关导入 =====================
from prometheus_client import start_http_server, Gauge, Counter, Summary
import threading

# ===================== 1. 全局配置（复用原有+新增监控端口） =====================
# 基础配置
SAVE_VIDEO = True  # 全局变量核心定义
DETECT_CONF = 0.5
DETECT_CLASSES = [0]  # 仅检测行人（COCO数据集ID=0）
VIDEO_PATH = "test.mp4"  # 替换为你的视频/摄像头绝对路径（摄像头填0）
# 新增：Prometheus监控配置
PROMETHEUS_PORT = 8000  # 指标暴露端口，与prometheus.yml中targets一致
COLLECT_INTERVAL_FRAME = 10  # 每处理10帧采集一次指标
# ==================================
# Redis配置
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0
REDIS_KEY = "cv_alert:default_rules"  # 告警规则Redis键名
# Kafka配置
KAFKA_BOOTSTRAP_SERVERS = ["127.0.0.1:9092"]
KAFKA_TOPIC = "cv_alert_topic"  # 告警消息Kafka主题
# 日志配置
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
ALERT_CSV_PATH = os.path.join(LOG_DIR, "alert_records.csv")

# ===================== 2. 新增：Prometheus指标定义（核心监控指标） =====================
# 计数器：累计值（只会增，不会减）
TOTAL_FRAMES = Counter('cv_total_frames', '处理的视频帧总数')  # 总处理帧数
TOTAL_ALERTS = Counter('cv_total_timeouts_alerts', '超时告警总次数')  # 超时告警总数
DETECT_SUCCESS = Counter('cv_detect_service_success', '检测服务调用成功次数')  # 检测成功次数
DETECT_FAILED = Counter('cv_detect_service_failed', '检测服务调用失败次数')  # 检测失败次数
TRACK_SUCCESS = Counter('cv_track_service_success', '跟踪服务调用成功次数')  # 跟踪成功次数
TRACK_FAILED = Counter('cv_track_service_failed', '跟踪服务调用失败次数')  # 跟踪失败次数

# 仪表盘：瞬时值（可增可减，反映当前状态）
CURRENT_ACTIVE_TRACK_ID = Gauge('cv_current_active_track_ids', '当前活跃的跟踪ID数量')  # 活跃ID数
CURRENT_FPS = Gauge('cv_current_fps', '当前每秒处理帧数（FPS）')  # 实时FPS

# 摘要：记录耗时（可选，暂不展示）
PROCESS_FRAME_TIME = Summary('cv_process_frame_seconds', '单帧处理耗时（秒）')

# ===================== 3. 日志初始化 =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "business_service.log"), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("BusinessService")

# ===================== 4. 本地告警记录函数（原有，无修改） =====================
def init_alert_csv():
    if not os.path.exists(ALERT_CSV_PATH):
        try:
            with open(ALERT_CSV_PATH, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                header = ["告警时间", "跟踪ID", "目标左上角X", "目标左上角Y", "目标右下角X", "目标右下角Y", "超时时间(秒)", "禁行区坐标", "处理帧号"]
                writer.writerow(header)
            logger.info(f"✅ 本地告警记录文件初始化成功：{ALERT_CSV_PATH}")
        except Exception as e:
            logger.warning(f"⚠️  本地告警记录文件初始化失败：{str(e)}")

def write_alert_to_local(alert_msg, forbidden_area):
    try:
        with open(ALERT_CSV_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            row_data = [alert_msg["timestamp"], alert_msg["track_id"], alert_msg["x1"], alert_msg["y1"], alert_msg["x2"], alert_msg["y2"], alert_msg["stay_seconds"], str(forbidden_area), alert_msg["frame_count"]]
            writer.writerow(row_data)
        logger.info(f"📝 本地告警记录写入成功：ID={alert_msg['track_id']}")
    except Exception as e:
        logger.warning(f"⚠️  本地告警记录写入失败：{str(e)}")

# ===================== 5. 第三方服务初始化（原有，新增Prometheus启动） =====================
# 初始化Redis连接
try:
    redis_connector = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True, socket_timeout=5)
    redis_connector.ping()
    logger.info("✅ Redis连接成功")
    default_rules = {"forbidden_area": json.dumps([[100, 100, 500, 500]]), "timeout": "10.0"}
    if not redis_connector.hgetall(REDIS_KEY):
        redis_connector.hset(REDIS_KEY, mapping=default_rules)
        logger.info("Redis初始化默认告警规则完成")
except Exception as e:
    logger.error(f"❌ Redis连接失败：{str(e)}", exc_info=True)
    raise SystemExit(1)

# 初始化Kafka生产者
try:
    producer = KafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS, retries=3, request_timeout_ms=5000, value_serializer=lambda x: json.dumps(x, ensure_ascii=False).encode("utf-8"))
    logger.info("✅ Kafka生产者初始化成功")
except Exception as e:
    logger.warning(f"⚠️  Kafka生产者初始化失败，将仅启用本地告警记录：{str(e)}")
    producer = None

# 初始化本地告警CSV
init_alert_csv()

# ===================== 新增：启动Prometheus指标服务（独立线程，不阻塞主流程） =====================
def start_prometheus_server():
    start_http_server(PROMETHEUS_PORT)
    logger.info(f"✅ Prometheus指标服务已启动，暴露端口：{PROMETHEUS_PORT}（可访问http://localhost:{PROMETHEUS_PORT}/metrics查看）")

# 启动独立线程运行Prometheus服务
prom_thread = threading.Thread(target=start_prometheus_server, daemon=True)
prom_thread.start()

# ===================== 6. gRPC客户端初始化（原有，无修改） =====================
def get_detect_client():
    channel = grpc.insecure_channel('localhost:50051', options=[('grpc.max_send_message_length', 1024*1024*10), ('grpc.max_receive_message_length', 1024*1024*10)])
    return cv_service_pb2_grpc.DetectServiceStub(channel)

def get_track_alert_client():
    channel = grpc.insecure_channel('localhost:50052', options=[('grpc.max_send_message_length', 1024*1024*20), ('grpc.max_receive_message_length', 1024*1024*20)])
    return cv_service_pb2_grpc.TrackAlertServiceStub(channel)

# ===================== 7. 工具函数（原有，无修改） =====================
def cv2frame_to_grpc(cv2_frame):
    frame_bytes = cv2_frame.tobytes()
    height, width, channels = cv2_frame.shape
    return cv_service_pb2.Frame(data=frame_bytes, width=width, height=height, channels=channels)

# ===================== 8. 核心业务逻辑（新增指标采集+更新） =====================
def main():
    global SAVE_VIDEO
    # 初始化gRPC客户端
    try:
        detect_client = get_detect_client()
        track_alert_client = get_track_alert_client()
        logger.info("✅ gRPC客户端初始化成功")
    except Exception as e:
        logger.error(f"❌ gRPC客户端初始化失败：{str(e)}", exc_info=True)
        return

    # 初始化视频流
    try:
        cap = cv2.VideoCapture(VIDEO_PATH)
        if not cap.isOpened():
            logger.error(f"❌ 视频流打开失败：{VIDEO_PATH}")
            return
        fps = int(cap.get(cv2.CAP_PROP_FPS))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        logger.info(f"✅ 视频流加载成功：FPS={fps} | 分辨率={width}x{height}")
    except Exception as e:
        logger.error(f"❌ 视频流初始化失败：{str(e)}", exc_info=True)
        return

    # 初始化视频写入器
    video_writer = None
    if SAVE_VIDEO:
        try:
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            video_writer = cv2.VideoWriter("output_detect.mp4", fourcc, fps, (width, height))
            logger.info("✅ 视频写入器初始化成功，将保存为output_detect.mp4")
        except Exception as e:
            logger.warning(f"⚠️  视频写入器初始化失败，不保存视频：{str(e)}")
            SAVE_VIDEO = False

    # 新增：FPS计算相关变量
    frame_count = 0
    start_time = time.time()
    last_collect_time = start_time

    try:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                logger.info("📹 视频流处理完成")
                break
            frame_count += 1
            TOTAL_FRAMES.inc()  # 新增：累计总帧数+1

            # 新增：计算实时FPS（每帧更新）
            current_time = time.time()
            elapsed_time = current_time - start_time
            if elapsed_time > 0:
                real_fps = frame_count / elapsed_time
                CURRENT_FPS.set(round(real_fps, 2))  # 更新实时FPS指标

            # 步骤1：加载Redis告警规则
            current_forbidden_area = [[100,100,500,500]]
            try:
                redis_rules = redis_connector.hgetall(REDIS_KEY)
                forbidden_area_list = json.loads(redis_rules.get("forbidden_area", json.dumps([[100,100,500,500]])))
                current_forbidden_area = forbidden_area_list
                forbidden_areas = [cv_service_pb2.ForbiddenArea(rect=area) for area in forbidden_area_list]
                timeout = float(redis_rules.get("timeout", 10.0))
                alert_rule = cv_service_pb2.AlertRule(areas=forbidden_areas, timeout=timeout)
            except Exception as e:
                logger.warning(f"⚠️  加载Redis规则失败，使用默认规则：{str(e)}")
                default_area = cv_service_pb2.ForbiddenArea(rect=[100,100,500,500])
                alert_rule = cv_service_pb2.AlertRule(areas=[default_area], timeout=10.0)

            # 步骤2：调用检测服务（新增：指标计数）
            grpc_frame = cv2frame_to_grpc(frame)
            try:
                detect_request = cv_service_pb2.DetectRequest(frame=grpc_frame, conf_thres=DETECT_CONF)
                detect_response = detect_client.Detect(detect_request)
                if detect_response.success:
                    DETECT_SUCCESS.inc()  # 检测成功+1
                else:
                    DETECT_FAILED.inc()  # 检测失败+1
                    logger.warning(f"⚠️  第{frame_count}帧检测失败：{detect_response.message}")
                    continue
            except Exception as e:
                DETECT_FAILED.inc()  # 调用异常也记为失败
                logger.warning(f"⚠️  调用检测服务失败：{str(e)}")
                continue

            # 步骤3：调用跟踪服务（新增：指标计数+活跃ID数采集）
            try:
                track_alert_request = cv_service_pb2.TrackAlertRequest(frame=grpc_frame, detections=detect_response.detections, alert_rule=alert_rule)
                track_alert_response = track_alert_client.TrackAndAlert(track_alert_request)
                if track_alert_response.success:
                    TRACK_SUCCESS.inc()  # 跟踪成功+1
                    # 新增：更新当前活跃跟踪ID数（从响应中获取）
                    active_id_count = len(set([alert.track_id for alert in track_alert_response.alerts]))
                    CURRENT_ACTIVE_TRACK_ID.set(active_id_count)
                else:
                    TRACK_FAILED.inc()  # 跟踪失败+1
                    logger.warning(f"⚠️  第{frame_count}帧跟踪告警失败：服务内部返回失败")
                    continue
            except Exception as e:
                TRACK_FAILED.inc()  # 调用异常记为失败
                logger.warning(f"⚠️  第{frame_count}帧跟踪告警失败：{str(e)}", exc_info=True)
                continue

            # 步骤4：解析标注帧
            try:
                annotated_frame = np.frombuffer(track_alert_response.frame.data, dtype=np.uint8).reshape(track_alert_response.frame.height, track_alert_response.frame.width, track_alert_response.frame.channels)
            except Exception as e:
                logger.warning(f"⚠️  解析标注帧失败：{str(e)}")
                annotated_frame = frame

            # 保存视频
            if SAVE_VIDEO and video_writer:
                video_writer.write(annotated_frame)

            # 步骤5：Kafka+本地告警（新增：累计告警数）
            for alert in track_alert_response.alerts:
                if alert.alert_type == 2:
                    TOTAL_ALERTS.inc()  # 新增：超时告警总数+1
                    alert_msg = {"track_id": alert.track_id, "x1": alert.x1, "y1": alert.y1, "x2": alert.x2, "y2": alert.y2, "stay_seconds": timeout, "frame_count": frame_count, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())}
                    # Kafka上报
                    if producer:
                        try:
                            producer.send(KAFKA_TOPIC, value=alert_msg)
                            logger.info(f"🚨 超时告警：ID={alert.track_id}，已上报Kafka")
                        except Exception as e:
                            logger.warning(f"⚠️  Kafka告警上报失败：{str(e)}")
                    # 本地写入
                    write_alert_to_local(alert_msg, current_forbidden_area)

            # 窗口显示+按键监听
            cv2.imshow("YOLO+DeepSORT+gRPC 微服务视觉告警系统", annotated_frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('1'):
                new_forbidden = [[200, 200, 600, 600]]
                new_timeout = 10.0
                redis_connector.hset(REDIS_KEY, mapping={"forbidden_area": json.dumps(new_forbidden), "timeout": str(new_timeout)})
                logger.info(f"✅ 告警规则已修改：禁行区={new_forbidden}，超时时间={new_timeout}秒")
            elif key == ord('2'):
                current_rule = redis_connector.hgetall(REDIS_KEY)
                new_forbidden = json.loads(current_rule.get("forbidden_area", json.dumps([[100,100,500,500]])))
                new_timeout = 5.0
                redis_connector.hset(REDIS_KEY, mapping={"forbidden_area": json.dumps(new_forbidden), "timeout": str(new_timeout)})
                logger.info(f"✅ 告警规则已修改：禁行区不变，超时时间={new_timeout}秒")
            elif key == ord('3'):
                default_forbidden = [[100, 100, 500, 500]]
                default_timeout = 10.0
                redis_connector.hset(REDIS_KEY, mapping={"forbidden_area": json.dumps(default_forbidden), "timeout": str(default_timeout)})
                logger.info(f"✅ 告警规则已恢复默认：{default_forbidden}，{default_timeout}秒")
            elif key == ord('q'):
                logger.info("👋 用户手动终止程序，开始释放资源...")
                break

    except KeyboardInterrupt:
        logger.info("👋 手动终止程序")
    except Exception as e:
        logger.error(f"❌ 主循环执行失败：{str(e)}", exc_info=True)
    finally:
        # 资源释放
        cap.release()
        if SAVE_VIDEO and video_writer:
            video_writer.release()
        cv2.destroyAllWindows()
        redis_connector.close()
        if producer:
            producer.flush()
            producer.close()
        # 打印最终监控指标
        logger.info(f"📊 项目运行结束 - 总处理帧数：{int(TOTAL_FRAMES._value.get())} | 总超时告警：{int(TOTAL_ALERTS._value.get())} | 检测成功率：{round(int(DETECT_SUCCESS._value.get())/(int(DETECT_SUCCESS._value.get())+int(DETECT_FAILED._value.get()))*100,2)}% | 跟踪成功率：{round(int(TRACK_SUCCESS._value.get())/(int(TRACK_SUCCESS._value.get())+int(TRACK_FAILED._value.get()))*100,2)}%")
        logger.info("✅ 所有资源已释放，程序正常退出")

if __name__ == "__main__":
    try:
        import ultralytics
        import deep_sort_realtime
    except ImportError as e:
        logger.error(f"❌ 缺少核心依赖，请执行：pip install ultralytics deep-sort-realtime opencv-python", exc_info=True)
        raise SystemExit(1)
    main()
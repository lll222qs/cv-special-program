# 企业级智能安防系统

[![Python Version](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-green)](https://fastapi.tiangolo.com/)
[![OpenCV](https://img.shields.io/badge/OpenCV-4.0%2B-orange)](https://opencv.org/)
[![YOLOv8](https://img.shields.io/badge/YOLOv8-Latest-red)](https://github.com/ultralytics/ultralytics)

一套完整的企业级智能安防监控系统，集成多摄像头管理、实时目标检测、智能告警、规则引擎、数据统计分析等核心功能，支持本地摄像头/视频文件/IP摄像头接入，提供可视化Web管理界面。

## ✨ 核心功能

| 功能模块 | 详细说明 |
|---------|---------|
| 📹 多摄像头管理 | 支持本地USB摄像头、视频文件模拟、IP摄像头（HTTP/RTSP）接入，实时视频流传输 |
| 🔍 智能目标检测 | 集成YOLOv8模型，实时检测人员，支持目标追踪和ID管理 |
| ⚠️ 智能告警系统 | 支持禁区闯入、人员滞留、人群聚集等多类型告警，分级告警机制 |
| 🧠 规则引擎 | 可配置化告警规则，支持自定义触发条件、冷却时间、优先级 |
| 📱 多渠道通知 | 支持钉钉机器人、邮件告警推送，分级提醒 |
| 📊 数据统计分析 | 告警趋势分析、系统状态监控、多维度数据可视化 |
| 💾 数据持久化 | SQLite数据库存储告警记录、摄像头配置、规则、统计数据 |
| 🌐 Web管理界面 | 一体化管理面板，支持实时监控、告警查看、规则配置、统计分析 |
| 🛠️ 系统监控 | CPU/内存使用率、帧率监控，设备状态实时展示 |

## 📋 技术栈

### 后端核心
- **Web框架**: FastAPI（高性能异步API）
- **视频处理**: OpenCV-Python
- **目标检测**: YOLOv8 (Ultralytics)
- **数据库**: SQLite3（轻量无需额外部署）
- **异步处理**: asyncio、threading
- **系统监控**: psutil

### 前端展示
- **原生HTML/CSS/JavaScript**（无需前端框架，开箱即用）
- **数据可视化**: Chart.js
- **视频流**: MJPEG实时传输

### 通知集成
- 钉钉机器人Webhook
- SMTP邮件推送

## 🚀 快速开始

### 环境要求
- Python 3.8+
- Windows/Linux/macOS
- 摄像头（可选，支持视频文件模拟）

### 安装步骤

#### 1. 克隆/下载项目
```bash
# 方式1: git克隆（如果使用git）
git clone https://github.com/your-username/security-system.git
cd security-system

# 方式2: 直接下载源码并解压
```

#### 2. 创建虚拟环境（推荐）
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/macOS
python3 -m venv venv
source venv/bin/activate
```

#### 3. 安装依赖
```bash
# 核心依赖
pip install fastapi uvicorn opencv-python numpy ultralytics psutil python-multipart

# 可选依赖（告警通知）
pip install requests  # 钉钉推送
pip install pycryptodome  # 钉钉签名
```

#### 4. 配置告警通知（可选）
修改代码中以下配置，适配你的通知渠道：
```python
# 钉钉配置
self.dingtalk_webhook = "你的钉钉机器人Webhook"
self.dingtalk_secret = "你的钉钉机器人密钥"

# 邮件配置
self.email_config = {
    "sender": "你的发件邮箱",
    "password": "邮箱授权码",
    "smtp_server": "smtp.qq.com",
    "smtp_port": 465,
    "receiver": "接收告警的邮箱"
}
```

#### 5. 启动系统
```bash
python security_system.py
```

#### 6. 访问系统
打开浏览器访问：`http://localhost:8000`

## 📖 使用指南

### 1. 实时监控
- 访问 `http://localhost:8000/live` 查看实时视频流
- 系统自动加载默认摄像头（ID: cam_001）
- 支持实时查看帧率、人员检测、区域告警信息

### 2. 告警管理
- 访问 `http://localhost:8000/alerts` 查看所有告警记录
- 支持按告警级别（紧急/严重/警告）、处理状态筛选
- 可标记告警为已处理，支持分页查询

### 3. 规则配置
- 访问 `http://localhost:8000/rules` 管理告警规则
- 默认包含3类规则：禁区闯入、人员滞留、人群聚集
- 支持启用/禁用规则、调整优先级和触发条件

### 4. 统计分析
- 访问 `http://localhost:8000/stats` 查看数据统计
- 告警趋势图表、级别分布饼图
- 系统性能指标监控

### 5. 添加自定义摄像头
```python
# 在代码中添加自定义摄像头
camera_mgr.add_camera(
    camera_id="cam_002",
    name="出入口摄像头",
    source="1",  # 摄像头ID或视频文件路径/IP地址
    source_type="local",  # local/video_file/http
    width=1280,
    height=720
)
camera_mgr.start_capture("cam_002")
```

### 6. 自定义警戒区域
修改 `check_alert_areas` 方法中的区域配置：
```python
alert_areas = [
    {"id": 1, "coords": [(100,100), (300,100), (300,300), (100,300)], "threshold": 1},
    {"id": 2, "coords": [(400,200), (600,200), (600,400), (400,400)], "threshold": 10},
    # 添加自定义区域...
]
```

## 🗄️ 数据库结构

### 核心表结构
1. **alerts** - 告警记录表
   - alert_id: 告警唯一标识
   - camera_id: 关联摄像头ID
   - alert_type: 告警类型（intrusion/loitering/crowd）
   - alert_level: 告警级别（emergency/critical/warning）
   - alert_time: 告警时间
   - processed: 处理状态

2. **cameras** - 摄像头配置表
   - camera_id: 摄像头唯一标识
   - source: 数据源（设备ID/文件路径/URL）
   - source_type: 源类型（local/video_file/http）
   - enabled: 是否启用

3. **rules** - 规则配置表
   - rule_id: 规则唯一标识
   - rule_type: 规则类型
   - conditions: 触发条件（JSON）
   - actions: 执行动作（JSON）
   - cooldown: 冷却时间

4. **stats** - 系统统计表
   - cpu_usage: CPU使用率
   - memory_usage: 内存使用率
   - fps: 视频帧率
   - timestamp: 统计时间

## 🔧 常见问题

### Q1: 摄像头无法打开？
- 检查摄像头ID是否正确（本地摄像头通常从0开始）
- 确保摄像头未被其他程序占用
- 尝试使用视频文件测试：`source="test.mp4"`

### Q2: YOLO模型加载失败？
- 确保网络正常，首次运行会自动下载YOLOv8n模型
- 手动下载模型：`yolov8n.pt` 放到项目根目录
- 检查ultralytics库版本：`pip install --upgrade ultralytics`

### Q3: 告警通知无法发送？
- 检查钉钉/邮箱配置是否正确
- 确保网络可以访问外部服务
- 查看控制台错误信息，排查认证问题

### Q4: 页面加载缓慢？
- 降低视频分辨率和帧率
- 关闭不必要的后台程序
- 调整JPEG压缩质量：`cv2.IMWRITE_JPEG_QUALITY, 70`

## 📌 待优化项

- [ ] 支持多摄像头同时展示
- [ ] 添加视频录制和截图功能
- [ ] 优化目标追踪算法
- [ ] 支持RTSP/RTMP流媒体协议
- [ ] 添加用户认证和权限管理
- [ ] 支持告警图片保存
- [ ] 移动端适配

## 📄 许可证

本项目基于 MIT 许可证开源，详情请查看 [LICENSE](LICENSE) 文件。

## 🙏 致谢

- [FastAPI](https://fastapi.tiangolo.com/) - 高性能Python Web框架
- [OpenCV](https://opencv.org/) - 计算机视觉库
- [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) - 目标检测模型
- [Chart.js](https://www.chartjs.org/) - 数据可视化库

## 📞 联系方式

- 作者：lll222qs
- 邮箱：1757952556@qq.com
- 项目地址：https://github.com/lll222qs/security-system

---

**注意**: 本系统仅用于学习和企业内部使用，请勿用于非法用途。使用前请确保遵守相关法律法规。

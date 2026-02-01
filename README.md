# 企业级CV分析平台（YOLOv9 + DeepSORT）
![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104%2B-green)
![Streamlit](https://img.shields.io/badge/Streamlit-1.28%2B-orange)
![YOLOv9](https://img.shields.io/badge/YOLOv9-Object%20Detection-red)
![DeepSORT](https://img.shields.io/badge/DeepSORT-Tracking-yellow)

一个基于 YOLOv9 + DeepSORT 构建的企业级计算机视觉分析平台，支持图片/视频/实时摄像头的目标检测、跟踪、行为识别、超分增强、深度估计等核心功能，提供可视化前端界面和标准化API接口。

## 🌟 核心功能
| 功能模块 | 详细说明 |
|----------|----------|
| 多模式处理 | 支持**图片批量上传**、**实时摄像头流**、**视频完整处理**三种模式 |
| 双跟踪算法 | DeepSORT（遮挡后ID稳定，适合视频/摄像头）、YOLO自带跟踪（速度快，适合单图片） |
| 超分增强 | 图像分辨率放大 + 去噪 + 边缘增强，提升小目标检测效果 |
| 深度估计 | 基于拉普拉斯算子的场景深度分析，可视化目标深度信息 |
| 行为识别 | 基于人体姿态关键点的行为判断（行走/站立/未知） |
| 轨迹可视化 | 实时绘制目标运动轨迹，支持轨迹持久化缓存 |
| 监控指标 | 集成Prometheus指标（请求数、推理耗时、活跃连接数等） |
| 自动化清理 | 定时清理临时视频文件，避免磁盘占用过高 |

## 📋 环境要求
- Python 3.8+
- 依赖库：见`requirements.txt`

## 🚀 快速开始

### 1. 克隆仓库
```bash
git clone https://github.com/你的用户名/enterprise-cv-platform.git
cd enterprise-cv-platform
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 下载模型权重
- YOLOv9c 权重：自动从ultralytics库下载（或手动放置`yolov9c.pt`到项目根目录）
- YOLOv8n-pose 权重：自动下载（姿态识别备用）
- DeepSORT 相关权重：已集成在`tracker`目录（无需额外下载）

### 4. 启动后端服务
```bash
python main.py
```
- 后端服务默认运行在 `http://0.0.0.0:8000`
- Prometheus指标地址：`http://localhost:8000/metrics`

### 5. 启动前端界面
```bash
streamlit run frontend.py
```
- 前端界面默认运行在 `http://localhost:8501`

## 📖 使用指南

### 1. 图片处理
1. 在前端选择「图片上传」模式
2. 配置跟踪算法（DeepSORT/YOLO）、超分倍数、置信度阈值等参数
3. 上传单张/多张图片（JPG/PNG）
4. 查看处理结果，支持导出分析后的图片

### 2. 实时摄像头分析
1. 选择「实时摄像头」模式
2. 配置跟踪算法（推荐DeepSORT）
3. 点击「启动摄像头」，实时查看目标检测、跟踪、行为识别结果
4. 点击「停止摄像头」关闭流

### 3. 视频处理
1. 选择「视频上传」模式
2. 配置处理参数，上传视频文件（MP4/AVI/MOV）
3. 等待处理完成（进度条实时显示）
4. 预览处理后的视频，支持下载

## 🔧 核心配置说明

### 后端配置（main.py）
| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| TEMP_VIDEO_DIR | "temp_videos" | 临时视频文件存储目录 |
| 检测类别 | [0,16] | 默认检测人（0）和狗（16），可修改`classes`参数调整 |
| 跟踪轨迹缓存 | 30帧 | 最多保留30帧轨迹，可修改`len(track_history[track_id]) > 30`调整 |
| 清理周期 | 1小时 | 定时清理24小时前的临时文件 |
| 端口 | 8000 | FastAPI服务端口 |

### 前端配置（frontend.py）
| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| API_BASE_URL | "http://localhost:8000" | 后端API地址，需与后端实际地址一致 |
| 摄像头分辨率 | 640x480 | 可修改`cap.set`参数调整 |
| 视频处理超时 | 300秒 | 可修改`timeout`参数调整 |

## 📡 API接口文档
启动后端后，访问 `http://localhost:8000/docs` 查看自动生成的Swagger API文档。

核心接口：
- `POST /process_image`：图片处理接口
- `POST /process_video_complete`：视频完整处理接口
- `GET /preview_video/{video_id}`：视频预览接口
- `WS /ws/video`：实时视频流WebSocket接口
- `GET /metrics`：Prometheus监控指标接口

## 📁 项目结构
```
enterprise-cv-platform/
├── main.py                # 后端核心服务（FastAPI + YOLOv9 + DeepSORT）
├── frontend.py            # 前端界面（Streamlit）
├── tracker/               # DeepSORT跟踪器模块
│   └── deep_sort_tracker.py
├── temp_videos/           # 临时视频存储目录（自动创建）
├── cv_service.log         # 日志文件（自动生成）
├── requirements.txt       # 依赖清单
└── README.md              # 项目说明
```

## 🚨 注意事项
1. 首次运行会自动下载YOLO模型权重，需保证网络通畅
2. 视频处理会生成临时文件，服务会自动清理（24小时过期）
3. 超分增强和深度估计会增加推理耗时，建议根据硬件配置调整参数
4. 实时摄像头模式需要本地有摄像头设备（或修改`cv2.VideoCapture(0)`为视频文件路径）

## 📊 监控指标
| 指标名 | 类型 | 说明 |
|--------|------|------|
| cv_requests_total | Counter | 总CV请求数 |
| cv_inference_time_seconds | Gauge | 单次请求推理耗时（秒） |
| cv_active_connections | Gauge | 活跃WebSocket连接数 |
| cv_image_process_total | Counter | 图片处理请求数 |
| cv_video_process_total | Counter | 视频处理请求数 |
| cv_camera_stream_total | Counter | 摄像头流会话数 |

## ✨ 技术栈
- 后端：FastAPI + Ultralytics YOLO + OpenCV + DeepSORT
- 前端：Streamlit
- 监控：Prometheus
- 调度：APScheduler
- 图像处理：OpenCV + NumPy

## 📄 许可证
本项目仅供学习和企业内部使用，如需商用请联系YOLOv9和DeepSORT原作者获取授权。

## 📞 问题反馈
如有问题，请提交Issue或联系作者：[你的邮箱/联系方式]

---
**备注**：替换文档中的「你的用户名」「你的邮箱/联系方式」为实际信息；可根据实际需求补充`requirements.txt`内容（如下）。

### requirements.txt 参考
```
fastapi>=0.104.1
uvicorn>=0.24.0
ultralytics>=8.0.200
opencv-python>=4.8.1.78
numpy>=1.24.4
streamlit>=1.28.2
requests>=2.31.0
websockets>=11.0.3
prometheus-client>=0.17.1
apscheduler>=3.10.4
matplotlib>=3.7.2
pandas>=2.0.3
Pillow>=10.0.1
```

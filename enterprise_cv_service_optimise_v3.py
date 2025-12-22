import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, WebSocket
from fastapi.responses import StreamingResponse, Response, FileResponse
from ultralytics import YOLO
import time
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
import io
import os
import uuid
import shutil
import datetime
# 新增定时任务+日志轮转依赖
import logging
from logging.handlers import RotatingFileHandler
from apscheduler.schedulers.background import BackgroundScheduler

# ========== 日志配置（替换原有的logging配置） ==========
# 日志文件轮转：100MB/文件，保留5个备份
file_handler = RotatingFileHandler(
    "cv_service.log",
    maxBytes=1024*1024*100,  # 100MB
    backupCount=5,
    encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s - %(message)s"
))
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(file_handler.formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler]
)
logger = logging.getLogger(__name__)

# ========== 1. 初始化组件 ==========
app = FastAPI(title="Enterprise CV Service")

# 创建临时文件目录（存储处理后的视频）
TEMP_VIDEO_DIR = "temp_videos"
os.makedirs(TEMP_VIDEO_DIR, exist_ok=True)

# 加载模型（CPU版本）
try:
    detect_model = YOLO("runs/detect/person_dog_final/weights/best.pt")
except Exception as e:
    logger.warning(f"自定义模型加载失败，使用默认YOLOv8n: {e}")
    detect_model = YOLO("yolov8n.pt")  # 兜底模型
pose_model = YOLO("yolov8n-pose.pt")

# ========== 监控指标（新增业务指标） ==========
# 基础指标
REQUEST_COUNT = Counter("cv_requests_total", "Total CV requests")
INFERENCE_TIME = Gauge("cv_inference_time_seconds", "Inference time per request")
ACTIVE_CONNECTIONS = Gauge("cv_active_connections", "Active WebSocket connections")
# 新增业务指标
IMAGE_PROCESS_COUNT = Counter("cv_image_process_total", "Total image processing requests")
VIDEO_PROCESS_COUNT = Counter("cv_video_process_total", "Total video processing requests")
CAMERA_STREAM_COUNT = Counter("cv_camera_stream_total", "Total camera stream sessions")

# ========== 2. 核心CV功能 ==========
# （这部分代码不变，保留原有的super_resolve/depth_estimation等函数）
# 超分增强
def super_resolve(frame, scale=2):
    upscaled = cv2.pyrUp(frame, dstsize=(frame.shape[1]*scale, frame.shape[0]*scale))
    denoised = cv2.bilateralFilter(upscaled, d=9, sigmaColor=150, sigmaSpace=150)
    gray = cv2.cvtColor(denoised, cv2.COLOR_BGR2GRAY)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    laplacian = np.uint8(np.absolute(laplacian))
    enhanced = cv2.addWeighted(denoised, 1.5, cv2.cvtColor(laplacian, cv2.COLOR_GRAY2BGR), -0.5, 0)
    return enhanced

# 深度估计
def depth_estimation(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    depth_map = np.abs(laplacian)
    depth_map = (depth_map - depth_map.min()) / (depth_map.max() - depth_map.min()) if depth_map.max() > depth_map.min() else depth_map
    depth_map = 1 - depth_map
    return depth_map

# 关键点容错
def safe_get_keypoint(pose_results, kp_index):
    if pose_results.keypoints is None:
        return None
    kp_data = pose_results.keypoints.data[0].cpu().numpy()
    if len(kp_data) <= kp_index:
        return None
    x, y, conf = kp_data[kp_index]
    if conf < 0.5:
        return None
    return (x, y, conf)

# 行为识别
def action_recognition(pose_results):
    action = "Unknown"
    r_knee = safe_get_keypoint(pose_results, 14)
    r_ankle = safe_get_keypoint(pose_results, 15)
    if r_knee and r_ankle:
        if abs(r_ankle[1] - r_knee[1]) > 30:
            action = "Walking"
        else:
            action = "Standing"
    return action

# 完整CV流水线
def cv_pipeline_custom(frame, scale=1, conf=0.3, enable_super_res=False, enable_depth=False):
    try:
        start_time = time.time()
        # 1. 超分增强
        enhanced_frame = frame
        if enable_super_res:
            enhanced_frame = super_resolve(frame, scale=int(scale))
        
        # 2. 检测跟踪
        track_results = detect_model.track(
            enhanced_frame, 
            classes=[0,16], 
            conf=float(conf),
            persist=True  # 开启跟踪持久化，更稳定
        )
        annotated_frame = track_results[0].plot()
        
        # 3. 深度估计
        if enable_depth:
            depth_map = depth_estimation(enhanced_frame)
            person_boxes = track_results[0].boxes.xyxy.cpu().numpy() if len(track_results[0].boxes) > 0 else []
            for box in person_boxes:
                x1, y1, x2, y2 = map(int, box)
                avg_depth = np.mean(depth_map[y1:y2, x1:x2]) if (y2-y1)*(x2-x1) > 0 else 1.0
                color = (int(255*avg_depth), 0, int(255*(1-avg_depth)))
                cv2.putText(
                    annotated_frame, 
                    f"Depth: {avg_depth:.2f}", 
                    (x1, y1-20), 
                    cv2.FONT_HERSHEY_SIMPLEX, 
                    0.8, 
                    color, 
                    2
                )
        
        # 4. 行为识别
        pose_results = pose_model(enhanced_frame, conf=0.6)
        action = action_recognition(pose_results[0])
        cv2.putText(
            annotated_frame, 
            f"Action: {action}", 
            (50, 50), 
            cv2.FONT_HERSHEY_SIMPLEX, 
            1, 
            (0, 255, 0), 
            2
        )
        INFERENCE_TIME.set(time.time() - start_time)
        return annotated_frame
    except Exception as e:
        logger.error(f"CV处理错误: {e}")
        return frame

# ========== 临时文件清理（新增定时任务） ==========
def clean_temp_videos():
    try:
        for file in os.listdir(TEMP_VIDEO_DIR):
            file_path = os.path.join(TEMP_VIDEO_DIR, file)
            if os.path.isfile(file_path):
                # 删除24小时前的文件
                if time.time() - os.path.getctime(file_path) > 86400:
                    os.remove(file_path)
                    logger.info(f"清理过期文件: {file_path}")
    except Exception as e:
        logger.error(f"临时文件清理失败: {e}")

# 启动时执行一次清理
clean_temp_videos()

# 新增：定时清理（每小时执行）
scheduler = BackgroundScheduler()
scheduler.add_job(clean_temp_videos, 'interval', hours=1)
scheduler.start()

# ========== 3. API接口（新增业务指标计数） ==========
# 监控指标接口
@app.get("/metrics")
async def metrics():
    metrics_data = generate_latest()
    return Response(content=metrics_data, media_type=CONTENT_TYPE_LATEST)

# 单图片处理接口（新增IMAGE_PROCESS_COUNT计数）
@app.post("/process_image")
async def process_image(
    file: UploadFile = File(...),
    scale: int = 1,
    conf: float = 0.3,
    enable_super_res: bool = False,
    enable_depth: bool = False
):
    try:
        REQUEST_COUNT.inc()
        IMAGE_PROCESS_COUNT.inc()  # 新增：图片处理计数
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            logger.warning("无效图片文件")
            return Response(content="无效图片", status_code=400)
        
        result_frame = cv_pipeline_custom(frame, scale=scale, conf=conf, enable_super_res=enable_super_res, enable_depth=enable_depth)
        _, img_encoded = cv2.imencode('.jpg', result_frame)
        return StreamingResponse(
            io.BytesIO(img_encoded.tobytes()), 
            media_type="image/jpeg"
        )
    except Exception as e:
        logger.error(f"图片处理错误: {e}")
        return Response(content=f"处理失败: {str(e)}", status_code=500)

# 视频完整处理接口（新增VIDEO_PROCESS_COUNT计数）
@app.post("/process_video_complete")
async def process_video_complete(
    file: UploadFile = File(...),
    scale: int = 1,
    conf: float = 0.3,
    enable_super_res: bool = False,
    enable_depth: bool = False
):
    try:
        REQUEST_COUNT.inc()
        VIDEO_PROCESS_COUNT.inc()  # 新增：视频处理计数
        # 1. 生成唯一视频ID
        video_id = str(uuid.uuid4())
        input_video_path = os.path.join(TEMP_VIDEO_DIR, f"{video_id}_input.mp4")
        output_video_path = os.path.join(TEMP_VIDEO_DIR, f"{video_id}_output.mp4")
        
        # 2. 保存上传的视频
        contents = await file.read()
        with open(input_video_path, "wb") as f:
            f.write(contents)
        
        # 3. 处理视频
        cap = cv2.VideoCapture(input_video_path)
        if not cap.isOpened():
            os.remove(input_video_path)
            logger.warning("无法打开视频文件")
            return Response(content="无法打开视频文件", status_code=400)
        
        # 获取视频参数
        fps = int(cap.get(cv2.CAP_PROP_FPS))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) * (scale if enable_super_res else 1)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) * (scale if enable_super_res else 1)
        fourcc = cv2.VideoWriter_fourcc(*'avc1')  # H.264编码
        
        # 创建视频写入器
        out = cv2.VideoWriter(output_video_path, fourcc, fps, (width, height))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        processed_frames = 0
        
        # 逐帧处理
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            
            # 处理单帧
            result_frame = cv_pipeline_custom(frame, scale=scale, conf=conf, enable_super_res=enable_super_res, enable_depth=enable_depth)
            out.write(result_frame)
            processed_frames += 1
        
        # 释放资源
        cap.release()
        out.release()
        os.remove(input_video_path)  # 删除输入临时文件
        logger.info(f"视频处理完成: {video_id}, 总帧: {total_frames}, 处理帧: {processed_frames}")
        
        # 返回视频ID
        return {
            "code": 200,
            "msg": "视频处理完成",
            "video_id": video_id,
            "total_frames": total_frames,
            "processed_frames": processed_frames
        }
    except Exception as e:
        logger.error(f"视频处理错误: {e}")
        return Response(content=f"处理失败: {str(e)}", status_code=500)

# 视频预览接口（不变）
@app.get("/preview_video/{video_id}")
async def preview_video(video_id: str):
    video_path = os.path.join(TEMP_VIDEO_DIR, f"{video_id}_output.mp4")
    if not os.path.exists(video_path):
        logger.warning(f"视频文件不存在: {video_id}")
        return Response(content="视频文件不存在", status_code=404)
    
    # 流式返回视频
    def video_stream():
        with open(video_path, "rb") as f:
            while chunk := f.read(1024*1024):  # 1MB分块
                yield chunk
    
    return StreamingResponse(
        video_stream(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f"inline; filename={video_id}_output.mp4"
        }
    )

# WebSocket实时摄像头接口（新增CAMERA_STREAM_COUNT计数）
@app.websocket("/ws/video")
async def websocket_video(websocket: WebSocket):
    await websocket.accept()
    ACTIVE_CONNECTIONS.inc()
    CAMERA_STREAM_COUNT.inc()  # 新增：摄像头会话计数
    logger.info("WebSocket连接建立")
    try:
        while True:
            data = await websocket.receive_bytes()
            nparr = np.frombuffer(data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                continue
            
            result_frame = cv_pipeline_custom(frame, scale=1, conf=0.3)
            _, img_encoded = cv2.imencode('.jpg', result_frame)
            await websocket.send_bytes(img_encoded.tobytes())
    except Exception as e:
        logger.error(f"WebSocket错误: {e}")
    finally:
        ACTIVE_CONNECTIONS.dec()
        await websocket.close()
        logger.info("WebSocket连接关闭")

# ========== 4. 服务启停钩子（新增） ==========
@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown()  # 停止定时任务
    logger.info("定时任务已停止，服务正常关闭")

# ========== 5. 启动服务（新增port_reuse=False） ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        reload=False,
        log_level="info",
         # 禁止端口复用，避免残留连接
    )
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
import logging
from logging.handlers import RotatingFileHandler
from apscheduler.schedulers.background import BackgroundScheduler
from tracker.deep_sort_tracker import CVDeepSORTTracker  # 导入DeepSORT跟踪器
from collections import defaultdict

# ========== 日志配置 ==========
file_handler = RotatingFileHandler(
    "cv_service.log",
    maxBytes=1024*1024*100,
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

# ========== 初始化组件 ==========
app = FastAPI(title="Enterprise CV Service (YOLOv9)")
TEMP_VIDEO_DIR = "temp_videos"
os.makedirs(TEMP_VIDEO_DIR, exist_ok=True)

# ========== 核心：加载YOLOv9模型 ==========
try:
    # 优先加载自定义训练的YOLOv9权重
    detect_model = YOLO("D:\\C language_2\\cv_project\\three_week1\\yolov9c.pt")
    logger.info("自定义YOLOv9模型加载成功")
except Exception as e:
    logger.warning(f"自定义模型加载失败，使用官方YOLOv9-c: {e}")
    detect_model = YOLO("yolov9c.pt")  # 兜底YOLOv9-c
pose_model = YOLO("yolov8n-pose.pt")  # 姿态模型暂保留v8（v9无pose版本）

# ========== 初始化DeepSORT跟踪器 ==========
tracker = CVDeepSORTTracker()
track_history = defaultdict(list)  # 轨迹持久化缓存

# ========== 监控指标 ==========
REQUEST_COUNT = Counter("cv_requests_total", "Total CV requests")
INFERENCE_TIME = Gauge("cv_inference_time_seconds", "Inference time per request")
ACTIVE_CONNECTIONS = Gauge("cv_active_connections", "Active WebSocket connections")
IMAGE_PROCESS_COUNT = Counter("cv_image_process_total", "Total image processing requests")
VIDEO_PROCESS_COUNT = Counter("cv_video_process_total", "Total video processing requests")
CAMERA_STREAM_COUNT = Counter("cv_camera_stream_total", "Total camera stream sessions")

# ========== 核心CV功能 ==========
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
    if depth_map.max() > depth_map.min():
        depth_map = (depth_map - depth_map.min()) / (depth_map.max() - depth_map.min())
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

# 完整CV流水线（YOLOv9+DeepSORT）
def cv_pipeline_custom(frame, scale=1, conf=0.3, enable_super_res=False, enable_depth=False, track_mode="DeepSORT"):
    try:
        start_time = time.time()
        # 1. 超分增强
        enhanced_frame = frame
        if enable_super_res:
            enhanced_frame = super_resolve(frame, scale=int(scale))
        
        # 2. YOLOv9检测（仅检测，不跟踪）
        detect_results = detect_model(
            enhanced_frame, 
            classes=[0,16], 
            conf=float(conf)
        )[0]
        
        # 3. 跟踪逻辑（按模式切换）
        annotated_frame = enhanced_frame.copy()
        if track_mode == "DeepSORT":
            # DeepSORT跟踪 + 轨迹持久化
            annotated_frame, tracks = tracker.process_frame(enhanced_frame, detect_results, conf)
            # 绘制历史轨迹
            for track in tracks:
                if not track.is_confirmed():
                    continue
                track_id = track.track_id
                ltrb = track.to_ltrb()
                center_x = int((ltrb[0] + ltrb[2]) / 2)
                center_y = int((ltrb[1] + ltrb[3]) / 2)
                track_history[track_id].append((center_x, center_y))
                if len(track_history[track_id]) > 30:
                    track_history[track_id].pop(0)
                # 绘制轨迹连线
                points = np.array(track_history[track_id], np.int32).reshape((-1, 1, 2))
                cv2.polylines(annotated_frame, [points], isClosed=False, color=(0, 255, 0), thickness=2)
        else:
            # YOLO自带跟踪（兼容原有逻辑）
            annotated_frame = detect_results.plot()
        
        # 4. 深度估计
        if enable_depth:
            depth_map = depth_estimation(enhanced_frame)
            for track in tracks if track_mode == "DeepSORT" else []:
                if not track.is_confirmed():
                    continue
                x1, y1, x2, y2 = map(int, track.to_ltrb())
                if (y2 - y1) > 0 and (x2 - x1) > 0:
                    avg_depth = np.mean(depth_map[y1:y2, x1:x2]) if depth_map is not None else 0.0
                    color = (int(255*avg_depth), 0, int(255*(1-avg_depth)))
                    cv2.putText(
                        annotated_frame, 
                        f"Depth: {avg_depth:.2f}", 
                        (x1, y1-40), 
                        cv2.FONT_HERSHEY_SIMPLEX, 
                        0.8, 
                        color, 
                        2
                    )
        
        # 5. 行为识别
        pose_results = pose_model(enhanced_frame, conf=0.6)[0]
        action = action_recognition(pose_results) or "Unknown"
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
        return frame.copy()

# ========== 定时任务：清理临时文件 ==========
def clean_temp_videos():
    try:
        for file in os.listdir(TEMP_VIDEO_DIR):
            file_path = os.path.join(TEMP_VIDEO_DIR, file)
            if os.path.isfile(file_path) and time.time() - os.path.getctime(file_path) > 86400:
                os.remove(file_path)
                logger.info(f"清理过期文件: {file_path}")
    except Exception as e:
        logger.error(f"临时文件清理失败: {e}")

clean_temp_videos()
scheduler = BackgroundScheduler()
scheduler.add_job(clean_temp_videos, 'interval', hours=1)
scheduler.start()

# ========== API接口 ==========
@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/process_image")
async def process_image(
    file: UploadFile = File(...),
    scale: int = 1,
    conf: float = 0.3,
    enable_super_res: bool = False,
    enable_depth: bool = False,
    track_mode: str = "DeepSORT"
):
    try:
        REQUEST_COUNT.inc()
        IMAGE_PROCESS_COUNT.inc()
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return Response(content="无效图片", status_code=400)
        
        result_frame = cv_pipeline_custom(frame, scale, conf, enable_super_res, enable_depth, track_mode)
        _, img_encoded = cv2.imencode('.jpg', result_frame)
        return StreamingResponse(io.BytesIO(img_encoded.tobytes()), media_type="image/jpeg")
    except Exception as e:
        logger.error(f"图片处理错误: {e}")
        return Response(content=f"处理失败: {str(e)}", status_code=500)

@app.post("/process_video_complete")
async def process_video_complete(
    file: UploadFile = File(...),
    scale: int = 1,
    conf: float = 0.3,
    enable_super_res: bool = False,
    enable_depth: bool = False,
    track_mode: str = "DeepSORT"
):
    try:
        REQUEST_COUNT.inc()
        VIDEO_PROCESS_COUNT.inc()
        tracker.reset()  # 重置跟踪器
        track_history.clear()  # 清空轨迹缓存
        
        video_id = str(uuid.uuid4())
        input_video_path = os.path.join(TEMP_VIDEO_DIR, f"{video_id}_input.mp4")
        output_video_path = os.path.join(TEMP_VIDEO_DIR, f"{video_id}_output.mp4")
        
        # 保存上传视频
        contents = await file.read()
        with open(input_video_path, "wb") as f:
            f.write(contents)
        
        # 处理视频
        cap = cv2.VideoCapture(input_video_path)
        if not cap.isOpened():
            os.remove(input_video_path)
            return Response(content="无法打开视频文件", status_code=400)
        
        fps = int(cap.get(cv2.CAP_PROP_FPS))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) * (scale if enable_super_res else 1)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) * (scale if enable_super_res else 1)
        fourcc = cv2.VideoWriter_fourcc(*'avc1')
        out = cv2.VideoWriter(output_video_path, fourcc, fps, (width, height))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        processed_frames = 0
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            result_frame = cv_pipeline_custom(frame, scale, conf, enable_super_res, enable_depth, track_mode)
            out.write(result_frame)
            processed_frames += 1
        
        cap.release()
        out.release()
        os.remove(input_video_path)
        logger.info(f"视频处理完成: {video_id}, 总帧: {total_frames}, 处理帧: {processed_frames}")
        
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

@app.get("/preview_video/{video_id}")
async def preview_video(video_id: str):
    video_path = os.path.join(TEMP_VIDEO_DIR, f"{video_id}_output.mp4")
    if not os.path.exists(video_path):
        return Response(content="视频文件不存在", status_code=404)
    
    def video_stream():
        with open(video_path, "rb") as f:
            while chunk := f.read(1024*1024):
                yield chunk
    return StreamingResponse(
        video_stream(),
        media_type="video/mp4",
        headers={"Content-Disposition": f"inline; filename={video_id}_output.mp4"}
    )

@app.websocket("/ws/video")
async def websocket_video(websocket: WebSocket):
    await websocket.accept()
    ACTIVE_CONNECTIONS.inc()
    CAMERA_STREAM_COUNT.inc()
    logger.info("WebSocket连接建立")
    
    tracker.reset()
    track_history.clear()
    
    try:
        # 获取跟踪模式参数
        query_params = websocket.query_params
        track_mode = query_params.get("track_mode", "DeepSORT")
        
        while True:
            data = await websocket.receive_bytes()
            nparr = np.frombuffer(data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                continue
            
            result_frame = cv_pipeline_custom(frame, 1, 0.3, False, False, track_mode)
            _, img_encoded = cv2.imencode('.jpg', result_frame)
            await websocket.send_bytes(img_encoded.tobytes())
    except Exception as e:
        logger.error(f"WebSocket错误: {e}")
    finally:
        ACTIVE_CONNECTIONS.dec()
        await websocket.close()
        logger.info("WebSocket连接关闭")

@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown()
    logger.info("定时任务已停止，服务正常关闭")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        reload=False,
        log_level="info"
    )
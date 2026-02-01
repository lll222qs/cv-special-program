# 添加上级目录到Python路径，解决gRPC文件导入问题
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

# 全局导入区：使用Python标准库线程池，移除gRPC废弃模块
import grpc
from concurrent.futures import ThreadPoolExecutor  # Python原生线程池，替代废弃模块
import cv2
import numpy as np
from ultralytics.utils.plotting import Annotator, colors
from deep_sort_realtime.deepsort_tracker import DeepSort
import cv_service_pb2
import cv_service_pb2_grpc
import logging

# 日志初始化
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("TrackAlertService")

# 初始化DeepSORT跟踪器
tracker = DeepSort(
    max_age=30,
    n_init=3,
    nn_budget=100,
    override_track_class=None,
    embedder="mobilenet",
    half=True,
    bgr=True,
    embedder_gpu=True,
    embedder_model_name=None,
    embedder_wts=None,
    polygon=False,
    today=None
)

# ========== 新增：轨迹存储字典 ==========
# 键：int类型track_id，值：最近20帧的中心点坐标列表
track_trajectories = {}
MAX_TRAJECTORY_LENGTH = 20  # 每个ID最多保存20个轨迹点

class TrackAlertServicer(cv_service_pb2_grpc.TrackAlertServiceServicer):
    def TrackAndAlert(self, request, context):
        try:
            # 1. 解析请求数据：视频帧+检测结果+告警规则
            frame = np.frombuffer(request.frame.data, dtype=np.uint8).reshape(
                request.frame.height, request.frame.width, request.frame.channels
            )
            # 核心修复：解除数组只读属性，允许OpenCV写操作
            frame = frame.copy()
            # 禁行区规则转换
            forbidden_areas = []
            for area in request.alert_rule.areas:
                rect = list(map(int, area.rect))
                forbidden_areas.append(rect)
            timeout = request.alert_rule.timeout

            # 2. 解析检测结果
            detections = []
            for det in request.detections:
                xyxy = list(map(int, [det.x1, det.y1, det.x2, det.y2]))
                conf = float(det.confidence)
                cls = int(det.class_id)
                if conf > 0.3:
                    detections.append((xyxy, conf, cls))

            # 3. DeepSORT目标跟踪
            tracks = tracker.update_tracks(detections, frame=frame)

            # 4. 绘制标注：帧+禁行区+跟踪框+告警状态+轨迹
            annotator = Annotator(frame, line_width=2, example="YOLO")
            # 绘制蓝色禁行区
            for (x1, y1, x2, y2) in forbidden_areas:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)

            # 遍历跟踪结果，判断告警状态并绘制
            alert_results = []
            for track in tracks:
                if not track.is_confirmed():
                    continue
                track_id = track.track_id
                # 核心修复1：强制转换track_id为整数，统一类型
                track_id_int = int(track_id)
                ltrb = track.to_ltrb()
                x1, y1, x2, y2 = list(map(int, ltrb))
                cls = track.get_det_class()

                # ========== 新增：计算中心点并更新轨迹 ==========
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2
                # 初始化轨迹列表（使用int类型id，避免字符串键）
                if track_id_int not in track_trajectories:
                    track_trajectories[track_id_int] = []
                # 添加新的中心点
                track_trajectories[track_id_int].append((center_x, center_y))
                # 保持轨迹长度不超过MAX_TRAJECTORY_LENGTH
                if len(track_trajectories[track_id_int]) > MAX_TRAJECTORY_LENGTH:
                    track_trajectories[track_id_int].pop(0)
                # ===============================================

                # 判断是否进入禁行区
                in_forbidden = False
                for (fx1, fy1, fx2, fy2) in forbidden_areas:
                    if fx1 < center_x < fx2 and fy1 < center_y < fy2:
                        in_forbidden = True
                        break

                # 告警状态判定
                alert_type = 0
                if in_forbidden:
                    alert_type = 1
                    track.alert_time = getattr(track, 'alert_time', 0) + 1
                    # 注意：timeout是秒，需乘以视频FPS（这里用10近似，匹配原逻辑）
                    if track.alert_time > timeout * 10:
                        alert_type = 2
                else:
                    track.alert_time = 0

                # 绘制跟踪框：按告警状态分色
                if alert_type == 0:
                    color = (0, 255, 0)  # 绿色-正常
                    label = f"ID:{track_id_int} Normal"
                elif alert_type == 1:
                    color = (0, 255, 255)  # 黄色-区域告警
                    label = f"ID:{track_id_int} In Forbidden"
                else:
                    color = (0, 0, 255)  # 红色-超时告警
                    label = f"ID:{track_id_int} Timeout Alert!"

                # ========== 修复后：绘制轨迹（int(track_id)避免报错） ==========
                trajectory = track_trajectories[track_id_int]
                if len(trajectory) >= 2:  # 至少2个点才画轨迹
                    # 用int类型id生成唯一颜色，避免字符串取模报错
                    traj_color = (
                        (track_id_int * 53) % 255,
                        (track_id_int * 127) % 255,
                        (track_id_int * 191) % 255
                    )
                    traj_points = np.array(trajectory, np.int32).reshape((-1, 1, 2))
                    cv2.polylines(frame, [traj_points], isClosed=False, color=traj_color, thickness=2)
                # ==================================

                # 绘制跟踪框和标签（带描边优化，更清晰）
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                # 标签位置自适应，避免超出画面
                label_pos = (x1, y1 - 10) if y1 - 10 > 10 else (x1, y1 + 20)
                # 黑色描边+白色文字，标签更醒目
                cv2.putText(frame, label, label_pos, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
                cv2.putText(frame, label, label_pos, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)

                # 构造AlertResult（使用int类型id，匹配gRPC协议定义）
                alert_results.append(cv_service_pb2.AlertResult(
                    track_id=track_id_int,
                    x1=int(x1),
                    y1=int(y1),
                    x2=int(x2),
                    y2=int(y2),
                    alert_type=int(alert_type)
                ))

            # 5. 转换标注后帧为gRPC响应格式
            frame_bytes = frame.tobytes()

            # 返回响应
            return cv_service_pb2.TrackAlertResponse(
                success=True,
                frame=cv_service_pb2.Frame(
                    data=frame_bytes,
                    width=frame.shape[1],
                    height=frame.shape[0],
                    channels=frame.shape[2]
                ),
                alerts=alert_results
            )
        except Exception as e:
            logger.error(f"跟踪告警失败：{str(e)}", exc_info=True)
            return cv_service_pb2.TrackAlertResponse(
                success=False
            )

def serve():
    server = grpc.server(ThreadPoolExecutor(max_workers=10))
    cv_service_pb2_grpc.add_TrackAlertServiceServicer_to_server(TrackAlertServicer(), server)
    server.add_insecure_port('[::]:50052')
    server.start()
    logger.info("✅ 跟踪告警服务已启动，监听端口：50052（已修复track_id类型问题）")
    server.wait_for_termination()

if __name__ == "__main__":
    serve()
# 添加上级目录到Python路径，解决模型/proto文件导入问题
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

# 全局导入：Python标准库+gRPC+YOLO+其他依赖
import grpc
from concurrent.futures import ThreadPoolExecutor
import cv2
import numpy as np
from ultralytics import YOLO
import cv_service_pb2
import cv_service_pb2_grpc
import logging

# 日志初始化
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("DetectService")

# 初始化YOLO模型（请确保模型文件在项目根目录，或修改为绝对路径）
# 若模型在weights目录：model = YOLO("weights/yolov9s.pt")
try:
    model = YOLO("D:\\C language_2\\cv_project\\three_week1\\yolov9c.pt")  # 适配你的YOLO模型文件
    logger.info("✅ YOLO模型加载成功")
except Exception as e:
    logger.error(f"❌ YOLO模型加载失败：{str(e)}", exc_info=True)
    raise SystemExit(1)

# 定义DetectService服务实现类（严格匹配proto中的服务名和方法名）
class DetectServiceServicer(cv_service_pb2_grpc.DetectServiceServicer):
    """
    实现cv_service.proto中定义的DetectService服务
    核心方法：Detect - 接收视频帧，返回YOLO检测结果
    """
    def Detect(self, request, context):
        """
        实现proto中定义的Detect方法
        :param request: DetectRequest（帧数据+置信度阈值）
        :param context: gRPC上下文
        :return: DetectResponse（检测结果+状态）
        """
        try:
            # 1. 解析gRPC请求：将Frame消息还原为OpenCV BGR帧
            frame = np.frombuffer(request.frame.data, dtype=np.uint8).reshape(
                request.frame.height, request.frame.width, request.frame.channels
            )
            conf_thres = request.conf_thres  # 置信度阈值（从客户端传入）

            # 2. YOLO目标检测（仅检测行人，class_id=0，可根据需求修改）
            results = model(frame, conf=conf_thres, classes=[0])
            detections = []
            for res in results:
                for box in res.boxes:
                    # 解析检测框：xyxy格式（x1,y1,x2,y2）
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    confidence = box.conf[0].cpu().numpy()
                    class_id = int(box.cls[0].cpu().numpy())
                    # 构造gRPC Detection消息，添加到结果列表
                    detections.append(cv_service_pb2.Detection(
                        x1=float(x1),
                        y1=float(y1),
                        x2=float(x2),
                        y2=float(y2),
                        confidence=float(confidence),
                        class_id=class_id
                    ))

            # 3. 构造gRPC检测响应（严格匹配DetectResponse协议定义）
            # 将原帧重新封装为gRPC Frame消息返回
            frame_bytes = frame.tobytes()
            grpc_frame = cv_service_pb2.Frame(
                data=frame_bytes,
                width=frame.shape[1],
                height=frame.shape[0],
                channels=frame.shape[2]
            )

            # 4. 返回响应（success=True+检测结果+原帧）
            return cv_service_pb2.DetectResponse(
                success=True,
                detections=detections,
                frame=grpc_frame,
                message="检测成功"  # proto中已定义message字段，可保留
            )

        except Exception as e:
            logger.error(f"检测失败：{str(e)}", exc_info=True)
            # 失败响应（仅返回success=False，适配proto定义）
            return cv_service_pb2.DetectResponse(
                success=False,
                detections=[],
                frame=cv_service_pb2.Frame(),
                message=f"检测失败：{str(e)}"
            )

def serve():
    """启动gRPC检测服务，监听50051端口，规范注册服务"""
    # 1. 创建gRPC服务器（使用Python标准库线程池，无废弃模块）
    server = grpc.server(ThreadPoolExecutor(max_workers=10))
    # 2. 严格按proto定义注册服务：DetectService + DetectServiceServicer实现
    cv_service_pb2_grpc.add_DetectServiceServicer_to_server(DetectServiceServicer(), server)
    # 3. 绑定端口：localhost:50051（与客户端调用地址一致）
    server.add_insecure_port('[::]:50051')
    # 4. 启动服务
    server.start()
    logger.info("✅ 检测服务已启动，监听端口：50051")
    # 5. 保持服务运行，等待请求
    server.wait_for_termination()

# 程序入口：直接启动服务
if __name__ == "__main__":
    serve()
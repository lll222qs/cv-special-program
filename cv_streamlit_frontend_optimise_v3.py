import streamlit as st
import cv2
import numpy as np
import requests
import asyncio
import websockets
import io
from PIL import Image
import time
import os
import matplotlib.pyplot as plt
import pandas as pd

# ========== 页面全局配置 ==========
st.set_page_config(
    page_title="企业级CV分析平台",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 后端接口地址
API_BASE_URL = "http://localhost:8000"

# 设置matplotlib中文字体
plt.rcParams["font.family"] = ["SimHei", "Microsoft YaHei", "WenQuanYi Micro Hei"]
plt.rcParams["axes.unicode_minus"] = False

# ========== 核心功能函数 ==========
# 1. 单图片处理
def process_image_api(image, scale=1, conf=0.3, enable_super_res=False, enable_depth=False):
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    img_byte_arr = img_byte_arr.getvalue()
    
    files = {"file": ("image.jpg", img_byte_arr, "image/jpeg")}
    params = {
        "scale": scale,
        "conf": conf,
        "enable_super_res": enable_super_res,
        "enable_depth": enable_depth
    }
    try:
        response = requests.post(
            f"{API_BASE_URL}/process_image",
            files=files,
            params=params,
            timeout=30
        )
        if response.status_code == 200:
            return Image.open(io.BytesIO(response.content))
        else:
            st.error(f"图片处理失败：HTTP {response.status_code}")
            return None
    except Exception as e:
        st.error(f"图片接口调用失败：{str(e)}")
        return None

# 2. 视频完整处理
def process_video_complete_api(video_file, scale=1, conf=0.3, enable_super_res=False, enable_depth=False):
    files = {"file": ("video.mp4", video_file, "video/mp4")}
    params = {
        "scale": scale,
        "conf": conf,
        "enable_super_res": enable_super_res,
        "enable_depth": enable_depth
    }
    try:
        progress_bar = st.progress(0)
        status_text = st.empty()
        status_text.text("开始处理视频...")
        
        response = requests.post(
            f"{API_BASE_URL}/process_video_complete",
            files=files,
            params=params,
            timeout=300  # 视频处理超时5分钟
        )
        
        progress_bar.progress(100)
        if response.status_code == 200:
            result = response.json()
            status_text.text(f"视频处理完成！共处理 {result['processed_frames']} 帧")
            return result["video_id"]
        else:
            status_text.text(f"视频处理失败：{response.text}")
            return None
    except Exception as e:
        st.error(f"视频接口调用失败：{str(e)}")
        return None

# 3. 实时摄像头处理
async def stream_camera(scale=1, conf=0.3, enable_super_res=False, enable_depth=False):
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    
    stframe = st.empty()
    stop_flag = st.session_state.get("stop_camera", False)
    
    try:
        async with websockets.connect(f"ws://localhost:8000/ws/video") as websocket:
            while cap.isOpened() and not stop_flag:
                ret, frame = cap.read()
                if not ret:
                    st.warning("无法读取摄像头画面")
                    break
                
                # 处理帧并发送
                _, img_encoded = cv2.imencode('.jpg', frame)
                await websocket.send(img_encoded.tobytes())
                
                # 接收处理后的帧
                processed_frame_data = await websocket.recv()
                processed_frame = cv2.imdecode(np.frombuffer(processed_frame_data, np.uint8), cv2.IMREAD_COLOR)
                
                # 转换颜色空间并显示
                processed_frame_rgb = cv2.cvtColor(processed_frame, cv2.COLOR_BGR2RGB)
                stframe.image(processed_frame_rgb, channels="RGB", use_column_width=True)
                
                # 检查停止信号
                if st.session_state.get("stop_camera", False):
                    break
    except Exception as e:
        st.error(f"摄像头连接失败：{str(e)}")
    finally:
        cap.release()
        st.success("摄像头已关闭")

# ========== 主页面逻辑 ==========
st.title("🏭 企业级CV分析平台")

# 初始化session state
if "stop_camera" not in st.session_state:
    st.session_state.stop_camera = False

# ========== 侧边栏配置 ==========
with st.sidebar:
    st.title("⚙️ 配置项")
    process_mode = st.radio(
        "处理模式", 
        ["图片上传", "实时摄像头", "视频上传"],
        key="process_mode_radio"
    )
    
    # 核心参数
    scale = st.slider("超分放大倍数", 1, 4, 1, help="1倍为原图，倍数越高处理越慢")
    conf_threshold = st.slider("检测置信度", 0.1, 1.0, 0.3, help="数值越高，检测越严格")
    
    # 功能开关
    enable_super_res = st.checkbox("启用超分增强", value=False, help="提升图片/视频分辨率（耗时增加）")
    enable_depth = st.checkbox("启用深度估计", value=False, help="生成深度图（耗时增加）")
    
    st.divider()
    st.info("✅ 视频处理完成后可直接预览/下载\n✅ 图片支持批量上传/导出")

# ========== 模式1：图片上传（批量+导出） ==========
if process_mode == "图片上传":
    st.subheader("🖼️ 图片上传分析（支持批量）")
    uploaded_files = st.file_uploader(
        "上传图片（JPG/PNG）",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="image_uploader"
    )
    
    if uploaded_files:
        for idx, uploaded_file in enumerate(uploaded_files):
            st.divider()
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader(f"原始图片 {idx+1}")
                original_image = Image.open(uploaded_file)
                st.image(original_image, use_column_width=True)
            
            with col2:
                st.subheader(f"分析结果 {idx+1}")
                with st.spinner(f"处理图片 {idx+1}..."):
                    result_image = process_image_api(
                        original_image,
                        scale=scale,
                        conf=conf_threshold,
                        enable_super_res=enable_super_res,
                        enable_depth=enable_depth
                    )
                    if result_image:
                        st.image(result_image, use_column_width=True)
                        # 导出按钮
                        img_byte_arr = io.BytesIO()
                        result_image.save(img_byte_arr, format='JPEG')
                        st.download_button(
                            label=f"📥 导出结果 {idx+1}",
                            data=img_byte_arr.getvalue(),
                            file_name=f"cv_analysis_{idx+1}_{int(time.time())}.jpg",
                            mime="image/jpeg",
                            key=f"download_btn_{idx}"
                        )

# ========== 模式2：实时摄像头 ==========
elif process_mode == "实时摄像头":
    st.subheader("📹 实时摄像头分析")
    
    col1, col2 = st.columns(2)
    with col1:
        start_btn = st.button("启动摄像头", key="start_camera")
    with col2:
        stop_btn = st.button("停止摄像头", key="stop_camera")
    
    if start_btn:
        st.session_state.stop_camera = False
        asyncio.run(stream_camera(
            scale=scale,
            conf=conf_threshold,
            enable_super_res=enable_super_res,
            enable_depth=enable_depth
        ))
    
    if stop_btn:
        st.session_state.stop_camera = True

# ========== 模式3：视频上传 ==========
elif process_mode == "视频上传":
    st.subheader("🎬 视频上传分析")
    uploaded_video = st.file_uploader(
        "上传视频（MP4/AVI/MOV）",
        type=["mp4", "avi", "mov"],
        key="video_uploader"
    )
    
    if uploaded_video:
        st.subheader("原始视频预览")
        st.video(uploaded_video)
        
        if st.button("开始处理视频", key="process_video"):
            video_id = process_video_complete_api(
                uploaded_video,
                scale=scale,
                conf=conf_threshold,
                enable_super_res=enable_super_res,
                enable_depth=enable_depth
            )
            
            if video_id:
                st.subheader("处理后视频预览")
                video_url = f"{API_BASE_URL}/preview_video/{video_id}"
                st.video(video_url)
                
                # 下载按钮
                st.download_button(
                    label="📥 下载处理后视频",
                    data=requests.get(video_url).content,
                    file_name=f"processed_video_{video_id}.mp4",
                    mime="video/mp4"
                )

# ========== 页脚 ==========
st.divider()
st.caption("© 2025 企业级CV分析平台 | 核心功能：超分增强、深度估计、目标检测、行为识别")
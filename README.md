# YOLO+DeepSORT 视觉告警系统（微服务架构版）
基于 gRPC 微服务架构的工业级视觉告警解决方案，集成 YOLO 实时行人检测、DeepSORT 多目标跟踪、Redis 动态配置、Kafka+CSV 双端持久化、Prometheus+Grafana 监控可视化等核心能力，通过服务拆分实现算法与业务解耦，支持独立部署、弹性扩展与可视化运维，适用于禁行区监控、超时滞留告警等安防场景。

## 一、项目核心亮点
### 1. 架构设计：高内聚低耦合微服务拆分
按「算法职责+业务流程」拆分三大独立服务，服务间通过 gRPC 标准化通信，支持单独升级、重启与扩容，解决单体应用"牵一发而动全身"的痛点：
- 检测服务（Detect Service）：专注 YOLO 行人检测，输出标准化检测结果
- 跟踪告警服务（Track Alert Service）：聚焦 DeepSORT 跟踪与告警规则判断，负责可视化绘制
- 业务服务（Business Service）：核心调度入口，集成中间件、监控采集与用户交互

### 2. 功能完整性：从算法到工程化全链路覆盖
✅ **算法核心**：YOLO 高精度行人检测 + DeepSORT 稳定跟踪（遮挡不丢 ID）  
✅ **动态配置**：Redis 存储告警规则，按键实时修改禁行区/超时时间（无需重启）  
✅ **多级告警**：进入禁行区（黄框）→ 超时滞留（红框）两级告警逻辑  
✅ **可视化增强**：彩色轨迹跟踪（每个 ID 唯一颜色）、描边标签（防遮挡）、禁行区高亮  
✅ **持久化保障**：Kafka 分布式上报 + 本地 CSV 兜底，告警信息永不丢失  
✅ **监控运维**：Prometheus 指标采集 + Grafana 可视化看板，实时监控 FPS、告警数等核心指标  
✅ **鲁棒性设计**：全流程异常捕获、资源优雅释放、中间件兼容降级（无 Kafka 也能运行）

### 3. 技术栈选型：工业级工程化组合
| 技术方向         | 核心组件                                  | 作用说明                                  |
|------------------|-------------------------------------------|-------------------------------------------|
| 目标检测         | YOLO（ultralytics）                       | 实时行人检测，输出坐标+置信度+类别         |
| 多目标跟踪       | DeepSORT（deep-sort-realtime）            | 目标关联与连续跟踪，生成唯一 Track ID      |
| 微服务通信       | gRPC + Protobuf                           | 跨服务高效数据传输，定义标准化接口         |
| 动态配置         | Redis                                     | 存储禁行区、超时时间，支持实时修改         |
| 告警持久化       | Kafka + CSV                               | 分布式上报与本地兜底，确保数据不丢失       |
| 监控可视化       | Prometheus + Grafana                      | 指标采集、时序存储与可视化监控            |
| 视频处理与展示   | OpenCV-Python                             | 视频读取、帧处理、可视化绘制              |
| 工程化支撑       | logging、threading、json                  | 独立日志、多线程、数据序列化              |

## 二、系统架构与数据流转
### 1. 整体架构图
```
┌─────────────────────────────────────────────────────────────┐
│ 客户端层：OpenCV 可视化窗口 + 视频文件/摄像头                │
└───────────────────────┬─────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────┐
│ 业务服务（Business Service） - 核心调度入口                  │
│ 核心能力：视频读取 → 规则加载 → gRPC 调用 → 持久化 → 监控采集 │
└───────┬───────────────────────────────┬─────────────────────┘
        │                               │
┌───────▼─────────────┐         ┌───────▼─────────────────────┐
│ 检测服务             │         │ 跟踪告警服务                 │
│ （Detect Service）   │         │ （Track Alert Service）      │
│ - YOLO 行人检测      │         │ - DeepSORT 目标跟踪          │
│ - 检测结果格式化     │         │ - 禁行区告警判断            │
│ - gRPC 服务端暴露    │         │ - 可视化绘制（框+轨迹+标签） │
│ - 端口：50051        │         │ - gRPC 服务端暴露            │
└─────────────────────┘         │ - 端口：50052                │
                                └─────────────────────────────┘
                                        │
┌───────────────────────────────────────┼─────────────────────┐
│ 中间件支撑层                          │                     │
│ ┌──────────┐  ┌──────────┐  ┌────────┐│  ┌───────────────┐  │
│ │  Redis   │  │  Kafka   │  │  CSV   ││  │ Prometheus+Grafana││
│ │ 配置存储 │  │ 告警上报 │  │ 本地存储││  │ 监控可视化    │  │
│ └──────────┘  └──────────┘  └────────┘│  └───────────────┘  │
└───────────────────────────────────────┴─────────────────────┘
```

### 2. 核心数据流转流程
1. 业务服务读取视频/摄像头帧数据，调用检测服务的 gRPC 接口；
2. 检测服务通过 YOLO 识别行人，返回标准化检测结果（坐标+置信度）；
3. 业务服务从 Redis 加载告警规则（禁行区+超时时间），将帧数据、检测结果、规则一并传给跟踪告警服务；
4. 跟踪告警服务通过 DeepSORT 分配唯一 Track ID，判断目标是否进入禁行区及滞留时间，绘制跟踪框、轨迹与告警标签；
5. 业务服务接收标注后帧数据，实时展示并保存视频，触发超时告警时同步写入 Kafka 与本地 CSV；
6. 业务服务实时采集系统指标（FPS、处理帧数、告警数等），暴露给 Prometheus；
7. Grafana 从 Prometheus 拉取指标，生成可视化监控看板。

## 三、快速部署指南
### 前置依赖
- 操作系统：Windows/Linux（推荐 Windows 本地测试，Linux 生产部署）
- Python 版本：3.8-3.10
- 中间件：Redis（默认 6379）、Kafka（默认 9092）、Prometheus（默认 9090）、Grafana（默认 3000）

### 1. 环境准备
#### （1）克隆仓库
```bash
git clone https://github.com/你的用户名/yolo-deepsort-alert-microservice.git
cd yolo-deepsort-alert-microservice
```

#### （2）创建虚拟环境并安装依赖
```bash
# 创建虚拟环境（推荐）
conda create -n cv-ms python=3.9 -y
conda activate cv-ms

# 安装核心依赖
pip install -r requirements.txt
```

#### （3）中间件启动
1. Redis：启动本地 Redis 服务（默认端口 6379，无需额外配置）；
2. Kafka：启动 Kafka 服务，创建告警主题：
   ```bash
   kafka-topics.bat --create --topic cv_alert_topic --bootstrap-server localhost:9092
   ```
3. Prometheus：替换解压目录下的 `prometheus.yml` 为项目根目录的配置文件，启动服务；
4. Grafana：安装后自动后台运行，访问 `http://localhost:3000` 即可（默认账号/密码：admin/admin）。

### 2. 服务启动（按顺序执行，各开独立终端）
#### 终端 1：启动检测服务
```bash
python detect-service/detect_service.py
```
> 启动成功标识：终端输出「✅ 检测服务已启动，监听端口：50051」

#### 终端 2：启动跟踪告警服务
```bash
python track-service/track_service.py
```
> 启动成功标识：终端输出「✅ 跟踪告警服务已启动，监听端口：50052」

#### 终端 3：启动业务服务（核心入口）
```bash
python business-service/business_service.py
```
> 启动成功标识：
> - 终端输出「✅ Prometheus 指标服务已启动，暴露端口：8000」
> - 弹出 OpenCV 可视化窗口
> - 访问 `http://localhost:8000/metrics` 可查看 `cv_` 前缀指标

### 3. Grafana 监控配置
#### （1）添加 Prometheus 数据源
1. 进入 Grafana 首页 → 左侧「Connections」→「Data sources」→「Add data source」；
2. 搜索「Prometheus」，配置 URL 为 `http://localhost:9090`，点击「Save & test」；
3. 提示「Data source is working」即为配置成功。

#### （2）创建监控看板
1. 左侧「Dashboards」→「New dashboard」→「Add visualization」；
2. 选择 Prometheus 数据源，配置核心指标组件：
   - 总处理帧数：`cv_total_frames`（可视化类型：Stat）
   - 实时 FPS：`cv_current_fps`（可视化类型：Stat+趋势图）
   - 活跃 Track ID 数：`cv_current_active_track_ids`（可视化类型：Stat）
   - 超时告警总数：`cv_total_timeouts_alerts`（可视化类型：Bar chart）
   - 服务成功率：`100*cv_detect_service_success/(cv_detect_service_success+cv_detect_service_failed)`（可视化类型：Stat）
3. 保存看板（命名为「视觉告警系统监控面板」），设置 10 秒自动刷新。

## 四、核心功能使用说明
### 1. 动态配置操作（可视化窗口按键）
| 按键 | 功能说明                                  | 生效方式                |
|------|-------------------------------------------|-------------------------|
| 1    | 修改禁行区为 [[200, 200, 600, 600]]       | 下一帧立即生效          |
| 2    | 修改超时时间为 5 秒（禁行区保持不变）      | 下一帧立即生效          |
| 3    | 恢复默认配置：禁行区 [[100,100,500,500]]+10 秒超时 | 下一帧立即生效 |
| q    | 优雅退出程序，释放所有资源（视频/中间件/窗口） | 即时生效                |

### 2. 可视化元素说明
| 元素         | 颜色/样式       | 含义说明                                  |
|--------------|-----------------|-------------------------------------------|
| 蓝色矩形框   | 实线边框        | 禁行区范围                                |
| 绿色跟踪框   | 实线边框+白色标签 | 正常行人（未进入禁行区）                  |
| 黄色跟踪框   | 实线边框+白色标签 | 告警一级（进入禁行区，未超时）            |
| 红色跟踪框   | 实线边框+白色标签 | 告警二级（进入禁行区，超时滞留）          |
| 彩色曲线     | 不同 ID 不同颜色 | 目标移动轨迹（保留最近 20 帧）            |

### 3. 告警记录查看
- **本地 CSV**：项目根目录 `alert_records.csv`，包含告警时间、Track ID、目标位置等字段，可直接用 Excel 打开；
- **Kafka 消息**：主题 `cv_alert_topic` 中存储 JSON 格式告警数据，支持后续消费分析。

## 五、项目目录结构
```
yolo-deepsort-alert-microservice/
├── detect-service/                # 检测服务目录
│   ├── __init__.py                # Python 包标识
│   └── detect_service.py          # 检测服务主程序（YOLO+gRPC 服务端）
├── track-service/                 # 跟踪告警服务目录
│   ├── __init__.py                # Python 包标识
│   └── track_service.py           # 跟踪告警主程序（DeepSORT+告警+绘制）
├── business-service/              # 业务服务目录
│   ├── __init__.py                # Python 包标识
│   └── business_service.py        # 核心调度程序（入口+中间件+监控）
├── cv_service.proto               # gRPC 协议定义文件（接口+消息类型）
├── cv_service_pb2.py              # Protobuf 编译后的消息类（运行依赖）
├── cv_service_pb2_grpc.py         # Protobuf 编译后的服务类（运行依赖）
├── prometheus.yml                 # Prometheus 监控配置文件
├── requirements.txt               # 项目依赖清单
├── README.md                      # 项目说明文档
├── .gitignore                     # Git 忽略文件
└── logs/                          # 日志目录（自动生成，按服务分类）
```

## 六、常见问题排查
### Q1：Prometheus 无法采集指标（targets 状态 DOWN）？
A1：
1. 确认业务服务已启动，且输出「Prometheus 指标服务已启动」；
2. 访问 `http://localhost:8000/metrics`，确认能看到 `cv_` 前缀指标；
3. 检查 8000 端口是否被占用（Windows：`netstat -ano | findstr 8000`）；
4. 重启 Prometheus 服务，确保配置文件已替换。

### Q2：按键修改配置不生效？
A2：
1. 检查 Redis 服务是否正常运行（默认 6379 端口）；
2. 查看业务服务日志，是否有「✅ 告警规则已修改」提示；
3. 若 Redis 未启动，程序将使用本地默认规则，修改不生效但不影响核心功能。

### Q3：跟踪告警服务报错「track_id 类型错误」？
A3：项目已兼容字符串/整数类型转换，若仍报错，检查 `track_service.py` 中 `track_id_int = int(track_id)` 代码是否存在。

### Q4：Kafka 未启动导致程序崩溃？
A4：业务服务已实现降级逻辑，Kafka 连接失败时仅跳过上报，保留本地 CSV 记录，若崩溃需检查 Kafka 初始化异常捕获代码是否完整。

## 七、扩展与优化方向
1. **算法加速**：集成 TensorRT/ONNX 优化 YOLO 模型，提升检测速度；
2. **多场景适配**：支持多禁行区配置、不同区域不同超时时间；
3. **告警推送**：集成邮件/企业微信/短信，触发告警时主动通知；
4. **Web 可视化**：开发前端页面，替代 OpenCV 窗口，支持远程访问；
5. **容器化部署**：编写 Dockerfile 与 Docker Compose，实现一键部署；
6. **服务注册发现**：集成 Consul/Nacos，支持多实例动态扩容。

## 八、许可证
本项目采用 MIT 开源许可证，允许自由使用、修改和分发，详情见 LICENSE 文件。

## 作者信息
- GitHub：lll222qs
- 项目地址：https://github.com/lll222qs/cv-special-program.git
- 备注：本项目为工业级计算机视觉微服务实践，适用于课程设计、项目开发与二次扩展，欢迎 Star 支持！

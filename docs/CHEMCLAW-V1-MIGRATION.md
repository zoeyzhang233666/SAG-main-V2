# ChemClaw V1 化工数据迁移

ChemClaw 以 SAG V2 作为产品主线和 Agent 编排层。V1 化工能力通过新增兼容层接入，不改写 V2 的 Agent、MCP、联网检索、向量检索和 3D 图谱实现。

## 从界面迁移

1. 打开“项目中心”并新建或选择一个化工项目。
2. 点击项目卡片上的“数据迁移”。
3. 选择 JSON、CSV/TSV 或 Markdown 文件。
4. 导入完成后打开图谱；列表、2D、3D 可以随时切换。

结构化导入不会再次执行内置切片，也不会重新抽取事件和实体。外部大模型生成的切片、事件、实体和关系会直接写入 V2 的关系图，并同时保存一份 V1 兼容信封。

## 支持的数据

- V1 完整备份：`schemaVersion: 1`，包含 `project`、`documents`、`chunks`、`events`、`entities` 等原字段。
- 简化 JSON：顶层可为 `documents`、`chunks`、`rows` 或数组。
- CSV/TSV：兼容 V1 中英文表头和实体多列格式。
- Markdown：兼容与 CSV 字段对应的表格。

为保证外部抽取结果完整，每个切片必须有正文和至少一个事件，每个事件必须有至少一个实体。旧 ID、来源、作者、时间、元数据及未知扩展字段会原样保存；导出时同时提供对应的 V2 系统 ID。

## 导出和备份

“数据迁移”弹窗支持：

- 完整或简化 JSON 导出；
- 完整或简化 CSV、Markdown 导出；
- 项目 ZIP 备份，包含 `manifest.json`、可重新导入的 `project.json` 和仍存在的原始上传文件。

对应 API：

- `POST /api/v1/sources/{source_id}/structured-import?format=auto`
- `GET /api/v1/sources/{source_id}/structured-export?format=json&profile=full`
- `GET /api/v1/sources/{source_id}/backup`

## 启动

```powershell
docker compose up -d --build
docker compose ps
```

默认访问 `http://localhost:3000`，API 健康检查为 `http://localhost:8000/api/v1/system/ready`。Compose 数据保存在原 `sagdata` 卷中；升级和重建容器不会修改字段或删除该卷。

# 工业异常报警操作模拟知识库语料包

本语料包为模拟工业场景文档，适合测试：

- DOCX 解析
- Markdown 解析
- 表格行级 chunk
- 图片占位与 image_id 绑定
- 报警码精确检索
- 设备异常问答
- Chroma 向量 RAG
- Neo4j / GraphRAG 实体关系抽取与融合检索

## 内容

- `docx/`：8 份 DOCX 操作手册，每份包含正常工况表、报警总览表、逐项报警处置卡、图片。
- `markdown/`：同内容的 Markdown 版本。
- `assets/images/`：模拟系统图和报警处理流程图。
- `metadata/alarm_catalog.csv`：80 条报警目录，可用于结构化入库或表格 RAG 测试。
- `metadata/entities_relations.jsonl`：面向 GraphRAG 的实体关系种子数据。
- `metadata/sample_questions.jsonl`：示例测试问题。
- `metadata/neo4j_schema_and_query_templates.cypher`：Neo4j 约束、索引和查询模板。

## 注意

这是模拟语料，不是任何真实工厂的操作规程。它故意包含一些容易混淆的信息，例如：

- 多个系统都有“压力高/温度高/振动高”，但原因和操作不同。
- 部分报警需要结合伴随报警判断，例如振动高可能来自汽蚀、不平衡、轴承故障或传感器松动。
- 表格行包含 `row_id`，图片包含 `image_id`，便于测试“命中表格行后带出同行图片”。
- JSONL 中包含 Alarm、Device、Parameter、Cause、Action、Image 等实体和关系，便于直接构建图谱。

## DOCX 文件名

- `DOC-P203_pump_alarm_manual.docx`
- `DOC-K401_air_compressor_alarm_manual.docx`
- `DOC-R101_reactor_temperature_fault_manual.docx`
- `DOC-CV302_conveyor_vfd_alarm_manual.docx`
- `DOC-CW501_cooling_water_alarm_manual.docx`
- `DOC-HLB21_bearing_vibration_diagnosis_manual.docx`
- `DOC-B701_boiler_safety_alarm_manual.docx`
- `DOC-TK110_tank_batching_alarm_manual.docx`

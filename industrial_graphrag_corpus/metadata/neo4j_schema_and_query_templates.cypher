// 模拟工业 GraphRAG 语料：Neo4j 约束与索引建议
CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE;
CREATE CONSTRAINT alarm_code IF NOT EXISTS FOR (a:Alarm) REQUIRE a.code IS UNIQUE;
CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE;
CREATE CONSTRAINT row_id IF NOT EXISTS FOR (r:TableRow) REQUIRE r.row_id IS UNIQUE;
CREATE CONSTRAINT image_id IF NOT EXISTS FOR (i:Image) REQUIRE i.image_id IS UNIQUE;
CREATE INDEX device_name IF NOT EXISTS FOR (d:Device) ON (d.name);
CREATE INDEX parameter_tag IF NOT EXISTS FOR (p:Parameter) ON (p.tag);

// 报警解释模板：参数 $alarm_code
MATCH (a:Alarm {code: $alarm_code})
OPTIONAL MATCH (a)-[:BELONGS_TO]->(d:Device)
OPTIONAL MATCH (a)-[:TRIGGERED_BY]->(p:Parameter)
OPTIONAL MATCH (a)-[:HAS_CAUSE]->(c:Cause)
OPTIONAL MATCH (a)-[:HAS_ACTION]->(act:Action)
OPTIONAL MATCH (r:TableRow)-[:MENTIONS]->(a)
OPTIONAL MATCH (r)-[:HAS_IMAGE]->(img:Image)
RETURN a, d, p, collect(DISTINCT c) AS causes, collect(DISTINCT act) AS actions, collect(DISTINCT r) AS rows, collect(DISTINCT img) AS images;

// 根据 chunk 扩展图谱证据：参数 $chunk_id
MATCH (ch:Chunk {chunk_id: $chunk_id})-[:MENTIONS]->(e)
OPTIONAL MATCH (e)-[rel]-(n)
RETURN ch, e, type(rel) AS relation, n;
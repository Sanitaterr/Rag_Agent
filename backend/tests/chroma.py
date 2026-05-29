import chromadb

client = chromadb.PersistentClient(
    path=r"D:\aaaHollySys\python\Rag_Agent\backend\storage\.chroma"
)

collection = client.get_collection("rag_docx_chunks")
print("count:", collection.count())

data = collection.get(
    include=["documents", "metadatas"]
)

for doc_id, text, meta in zip(data["ids"], data["documents"], data["metadatas"]):
    print("ID:", doc_id)
    print("META:", meta)
    print("TEXT:", text[:300])
    print("-" * 80)
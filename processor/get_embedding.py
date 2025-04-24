def get_embedding(text: str, project: str, location: str) -> list[float]:
    client = genai.Client(vertexai=True, project=project, location=location)
    response = client.models.embed_content(
        model="text-multilingual-embedding-002",
        contents=text
    )
    return response.embeddings[0].values

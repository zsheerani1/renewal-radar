import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

resp = client.models.generate_content(
    model="gemini-3.6-flash",
    contents="What finance or ERP system does Corinthia Hotels use? Cite sources.",
    config=types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        max_output_tokens=2000,
    ),
)

print(resp.text)
print("--- SOURCES ---")
meta = resp.candidates[0].grounding_metadata
for chunk in (meta.grounding_chunks or []):
    print("-", chunk.web.title, chunk.web.uri)
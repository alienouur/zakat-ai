import os

# tests never talk to Gemini: the template pipeline and the deterministic scope rules are what is under test
os.environ.setdefault("GEMINI_API_KEY", "")
os.environ.setdefault("ZAKAT_DATA_DIR", "/tmp/zakat-ai-tests")  # never write the real history DB from tests
os.environ.setdefault("REQUIRE_LOGIN", "0")  # existing pipeline/scope tests call the API anonymously

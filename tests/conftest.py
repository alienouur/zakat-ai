import os

# tests never talk to Gemini: the template pipeline and the deterministic scope rules are what is under test
os.environ.setdefault("GEMINI_API_KEY", "")

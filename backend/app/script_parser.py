import json

from PyPDF2 import PdfReader
from docx import Document

PARSE_PROMPT = """You are parsing an energy supplier compliance script into structured checkpoints.

Each checkpoint is a section of the script that an agent MUST cover during a call. Extract every distinct section.

SCRIPT TEXT:
{script_text}

For each section, provide:
- section: sequential number starting from 1
- name: short descriptive name (e.g., "Recording Disclosure", "Third Party Declaration", "Credit Vetting")
- required: what must be said in this section (summarize the key content)
- key_phrases: list of specific words/phrases that indicate this section was covered
- customer_response_required: true if the customer must give a clear "Yes" or confirmation
- strictness: how strictly the agent must follow the script for this section:
  - "verbatim" if the script says "MUST BE READ VERBATIM", "word for word", or similar exact-wording instructions
  - "customer_yes" if the section requires a clear "Yes" from the customer or customer confirmation to proceed
  - "mandatory" for all other sections where the meaning must be conveyed but exact wording is not required

Respond ONLY with valid JSON array, no other text:
[
  {{
    "section": 1,
    "name": "section name",
    "required": "what must be said",
    "key_phrases": ["phrase1", "phrase2"],
    "customer_response_required": false,
    "strictness": "mandatory"
  }}
]"""


def extract_text_from_pdf(file_path: str) -> str:
    reader = PdfReader(file_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
    return text.strip()


def extract_text_from_docx(file_path: str) -> str:
    doc = Document(file_path)
    text = ""
    for para in doc.paragraphs:
        text += para.text + "\n"
    return text.strip()


def extract_text_from_md(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def extract_text(file_path: str) -> str:
    lower = file_path.lower()
    if lower.endswith(".pdf"):
        return extract_text_from_pdf(file_path)
    elif lower.endswith(".docx"):
        return extract_text_from_docx(file_path)
    elif lower.endswith(".md") or lower.endswith(".markdown") or lower.endswith(".txt"):
        return extract_text_from_md(file_path)
    else:
        raise ValueError(f"Unsupported file type: {file_path}")


def checkpoints_to_markdown(supplier_name: str, script_name: str, mode: str, checkpoints: list[dict]) -> str:
    """Render parsed checkpoints as a clean markdown document the agent can ingest."""
    lines = [
        f"# {supplier_name} — {script_name}",
        "",
        f"_Mode: {mode}_",
        "",
        "Each section below is a checkpoint the agent must cover. Edit freely — these lines are sent to the LLM on every call analysis.",
        "",
        "---",
        "",
    ]
    for cp in checkpoints:
        sec = cp.get("section", "?")
        name = cp.get("name", "Unnamed").strip()
        strictness = cp.get("strictness", "mandatory")
        customer = cp.get("customer_response_required", False)
        required = (cp.get("required") or "").strip()
        key_phrases = cp.get("key_phrases") or []

        strictness_label = {
            "mandatory": "Meaning",
            "verbatim": "Word for Word",
            "customer_yes": "Meaning + Customer ✓",
        }.get(strictness, strictness)

        lines.append(f"## {sec}. {name}")
        lines.append(f"`{strictness_label}`" + (" · `Customer confirmation required`" if customer and strictness != "customer_yes" else ""))
        lines.append("")
        if required:
            lines.append(f"**Required:** {required}")
            lines.append("")
        if key_phrases:
            lines.append("**Key phrases:** " + ", ".join(f"`{p}`" for p in key_phrases))
            lines.append("")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


async def parse_script_to_checkpoints(file_path: str) -> list[dict]:
    script_text = extract_text(file_path)

    if not script_text or len(script_text) < 50:
        raise ValueError("Could not extract meaningful text from the file")

    prompt = PARSE_PROMPT.replace("{script_text}", script_text)

    from app.analysis import _call_llm
    content = await _call_llm(prompt, timeout=60.0)

    checkpoints = json.loads(content)
    return checkpoints

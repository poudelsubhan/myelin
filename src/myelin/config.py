import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    base_url: str
    model: str
    live: bool
    headless: bool
    runs_dir: Path
    crm_url: str
    demo_token: str = field(repr=False)
    demo_email: str
    demo_password: str = field(repr=False)
    timeout_s: int

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        return cls(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1",
            model=os.getenv("MYELIN_MODEL", "gpt-6-astra"),
            live=os.getenv("MYELIN_LIVE", "0") == "1",
            headless=os.getenv("MYELIN_HEADLESS", "0") == "1",
            runs_dir=Path(os.getenv("MYELIN_RUNS_DIR", str(ROOT / "runs"))),
            crm_url=os.getenv("MYELIN_CRM_URL", "http://localhost:8101"),
            demo_token=os.getenv("MYELIN_DEMO_TOKEN", ""),
            demo_email=os.getenv("MYELIN_DEMO_EMAIL", "demo@myelin.local"),
            demo_password=os.getenv("MYELIN_DEMO_PASSWORD", "myelin-demo"),
            timeout_s=int(os.getenv("MYELIN_RUN_TIMEOUT_S", "180")),
        )

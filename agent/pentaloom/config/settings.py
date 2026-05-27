"""PentaLoom 全局配置.

从环境变量 / .env 读, 用 pydantic-settings 校验.
"""

from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

AGENT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(AGENT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Novita 中转 ---
    anthropic_api_key: str = Field(..., alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(
        "https://api.novita.ai/anthropic", alias="ANTHROPIC_BASE_URL"
    )

    # --- 模型 ---
    model: str = Field("pa/claude-opus-4-7", alias="PENTALOOM_MODEL")

    # --- 运行模式 ---
    debug: bool = Field(False, alias="PENTALOOM_DEBUG")
    access_log: bool = Field(False, alias="PENTALOOM_ACCESS_LOG")

    # --- HTTP 服务 ---
    host: str = Field("127.0.0.1", alias="PENTALOOM_HOST")  # 桌面 app 只听 loopback
    port: int = Field(8090, alias="PENTALOOM_PORT")

    # --- CORS ---
    # 前端 dev server 走 5273 (Vite 5173+100 避冲突). Electron Renderer file:// origin 是 null,
    # "*" 兜底 (后端只听 loopback, 外部不可达).
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5273",
            "http://127.0.0.1:5273",
            "*",
        ]
    )

    # --- 数据目录 ---
    # 开发: agent/pentaloom-data/ (gitignore)
    # 产版: Electron Main 可注入 PENTALOOM_DATA_DIR 指到 user data dir
    data_dir: Path = Field(
        default=AGENT_ROOT / "pentaloom-data", alias="PENTALOOM_DATA_DIR"
    )

    @computed_field
    @property
    def db_path(self) -> Path:
        return self.data_dir / "pentaloom.db"

    @computed_field
    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @computed_field
    @property
    def sandboxes_root(self) -> Path:
        """每个 ChatSession 的私有沙箱目录都建在这里 (子目录名 = session_id).

        当作主 cwd 给 SDK, 跟用户挂载的目录 (add_dirs) 并行存在.
        """
        return self.data_dir / "sandboxes"

    @computed_field
    @property
    def python_env_dir(self) -> Path:
        """共享 uv project 目录, 给 install_python_libs / run_python_script 用.

        策略: 所有 session 共享同一个 venv (起步方案, 见 docs/file-capability.md §4.3).
        启动时 lifespan 异步预热: uv venv + uv add 预装一批高频包.
        """
        return self.data_dir / "python-env"

    def sandbox_dir_for(self, session_id: str) -> Path:
        return self.sandboxes_root / session_id

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sandboxes_root.mkdir(parents=True, exist_ok=True)
        self.python_env_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

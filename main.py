"""开发环境启动入口。

uv run python main.py
等价于：uv run uvicorn app.main:app --reload
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)

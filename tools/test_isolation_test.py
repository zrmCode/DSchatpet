"""测试隔离验证:跑测试/工具**绝不能**动到用户的真实数据。

这条测试是为一个真实事故写的:审计脚本与若干工具只设了 ``persist_config = False``,
而那只管住 ``config.json``;``memory/profile.json``、``memory/memories.jsonl``、
``memory/history.jsonl`` 仍指向用户的**真实养成档案** ——
一次跑测试就把测试假服务器的罐头回复(「你困了吗 / 我有点困了…」)写进了真实历史,
亲密度凭空 +9;设置面板的「保存」还会绕过 ``persist_config`` 直接把测试配置写进
用户的 ``config.json``(实测:把 chat_enabled 覆盖成 false,之后对话功能全"莫名不可用")。

判据:**对真实文件取 SHA256,跑完一圈操作后必须一模一样**;
同时 ``.tmp/testmode/<名字>/`` 里要有沙盒数据(证明操作确实发生了,不是"什么都没做")。

用法::

    .venv\\Scripts\\python.exe tools\\test_isolation_test.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_results = {"pass": 0, "fail": 0}

#: 用户的真实文件(测试结束后必须原封不动)
REAL_FILES = (
    ROOT / "config.json",
    ROOT / "memory" / "profile.json",
    ROOT / "memory" / "memories.jsonl",
    ROOT / "memory" / "history.jsonl",
)


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


def fingerprint(path: Path) -> str:
    """内容指纹;文件不存在也算一种确定状态。"""
    if not path.exists():
        return "<不存在>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    """假对话服务:回一句固定的话(与真实数据无关)。"""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps({"choices": [{"message": {
            "role": "assistant", "content": "隔离测试用的假回复"}}]},
            ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    before = {p: fingerprint(p) for p in REAL_FILES}
    print("真实数据基线:")
    for path, digest in before.items():
        rel = path.relative_to(ROOT)
        print(f"  {rel}  {digest[:16]}{'…' if digest != '<不存在>' else ''}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    import live2d.v3 as live2d
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from pet import config as config_mod
    from pet.actions import load_actions
    from pet.window import PetWindow

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    live2d.init()
    app = QApplication(sys.argv[:1])

    cfg = config_mod.Config.load()
    cfg.chat_base_url = f"http://127.0.0.1:{port}"
    cfg.chat_api_key = ""
    cfg.chat_api_key_env = "NO_SUCH_ENV_XYZ"
    cfg.chat_use_dsh_credentials = False
    cfg.gaze_follow = False
    cfg.idle_motion = False
    cfg.idle_autonomy = False
    cfg.idle_llm_thoughts = False
    cfg.poke_reaction = True

    model_dir = config_mod.find_model_dir()
    if model_dir is None:
        print("本机没有模型,跳过(见 assets/README.md)")
        return 0
    window = PetWindow(cfg, model_dir, config_mod.find_model_json(model_dir),
                       load_actions(model_dir))

    print("\n[1] 统一入口 enable_test_mode 必须同时隔离配置与养成数据")
    window.enable_test_mode("isolation")
    sandbox = ROOT / ".tmp" / "testmode" / "isolation"
    check("persist_config 已关闭", window.persist_config is False)
    check("档案路径指向沙盒", str(window.profile_path).startswith(str(sandbox)),
          str(window.profile_path))
    check("记忆库指向沙盒", sandbox in Path(window.memories.path).parents
          or Path(window.memories.path).parent == sandbox, str(window.memories.path))
    check("历史文件指向沙盒", Path(window.history.path).parent == sandbox,
          str(window.history.path))
    check("沙盒目录已创建", sandbox.is_dir())

    window.show()

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    pump(4.0)
    window.input_timer.stop()

    print("\n[2] 发一句话 → 只许写沙盒")
    window.send_message("隔离测试:这句话不该出现在你的真实记录里")
    pump(3.0)
    hist = Path(window.history.path)
    check("沙盒历史里有东西了", hist.exists() and hist.stat().st_size > 0, str(hist))
    memories = Path(window.memories.path)
    check("沙盒档案里有东西了", Path(window.profile_path).exists(), str(window.profile_path))

    print("\n[3] 设置面板点保存 → 写到沙盒,不碰用户的 config.json")
    dialog = window.make_settings_dialog()
    dialog.show()
    pump(0.5)
    dialog._on_save()
    pump(0.5)
    check("面板保存后 config.json 未被改动", fingerprint(ROOT / "config.json") == before[ROOT / "config.json"])
    sandbox_cfg = Path(window.config_path)
    check("保存确实走了落盘路径(写到了沙盒 config.json)",
          sandbox_cfg.is_file() and sandbox_cfg.stat().st_size > 0, str(sandbox_cfg))
    check("沙盒配置里是测试值", json.loads(sandbox_cfg.read_text(encoding="utf-8"))["gaze_follow"] is False
          if sandbox_cfg.is_file() else False)
    dialog.close()
    pump(0.3)

    print("\n[4] 戳一下 → 也不许写真实档案")
    window._fire_poke(0.2)
    pump(3.0)

    print("\n[5] 收尾核对:四个真实文件必须与基线完全一致")
    after = {p: fingerprint(p) for p in REAL_FILES}
    for path in REAL_FILES:
        rel = path.relative_to(ROOT)
        same = after[path] == before[path]
        check(f"{rel} 未被改动", same, f"{before[path][:12]}… → {after[path][:12]}…")

    window.hide()
    window.close()
    server.shutdown()
    app.quit()
    print(f"\n=== 通过 {_results['pass']} 项,失败 {_results['fail']} 项 ===")
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

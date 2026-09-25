"""发布前自检:把「git 真正会公布出去的东西」过一遍。

开源翻车通常不是代码写错,而是这几件:

  1. 把 `config.json`(可能有明文 API Key)、`pet.log`、`memory/`(养成档案 = 个人隐私)
     一起提交了;
  2. 代码/文档里硬编码了本机绝对路径(`C:\\Users\\某某\\...`、`E:\\...`)或自己的名字;
  3. 把不该分发的模型文件提交了(版权归原作者,只随 Release 压缩包提供);
  4. 混进几百 MB 的构建产物,仓库变得没人愿意 clone。

本脚本**按 git 的视角**审计:仓库已就绪就用 `git ls-files`(逐字等于将要公开的文件列表),
还没 init 就退化成"按 .gitignore 规则走一遍目录"。

用法::

    .venv\\Scripts\\python.exe tools\\preflight_check.py
    .venv\\Scripts\\python.exe tools\\preflight_check.py --all   # 连未跟踪文件一起审

退出码 0 = 可以发布;1 = 有必须处理的问题。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_results = {"pass": 0, "warn": 0, "fail": 0}

#: 这些**绝不能**出现在公开仓库里的路径前缀(运行时产物 / 个人数据 / 第三方模型)
FORBIDDEN_PREFIXES = (
    "config.json",
    "pet.log",
    "pet-fatal.log",
    "pet.lock",
    "pet.lock.pid",
    "memory/",
    "shots/",
    "dist/",
    "build/work/",
    ".venv/",
    ".tmp/",
    "assets/model/",
)

#: 开源必备文件
REQUIRED = (
    "LICENSE",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    ".gitignore",
    "requirements.txt",
    "assets/README.md",
    "assets/许可/模型使用须知.txt",
)

#: 只对这些后缀做内容扫描(二进制跳过)
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".ps1", ".iss", ".bat", ".spec",
                 ".yml", ".yaml", ".toml", ".cfg", ".ini", ""}

KEY_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),                    # OpenAI 风格
    re.compile(r'"chat_api_key"\s*:\s*"[^"\s]{8,}"'),          # 配置里填了真 Key
    re.compile(r"(?i)\b(api[_-]?key|secret|token)\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{24,}"),
)

MAX_FILE_MB = 5.0        # 单个文件超过这个大小要解释一下(模型已排除,正常都在 1 MB 内)
HARD_LIMIT_MB = 50.0     # GitHub 会警告的大文件


def check(name: str, ok: bool, detail: str = "", warn_only: bool = False) -> None:
    if ok:
        _results["pass"] += 1
        print(f"  [OK]   {name}")
    elif warn_only:
        _results["warn"] += 1
        print(f"  [注意] {name} {detail}")
    else:
        _results["fail"] += 1
        print(f"  [FAIL] {name} {detail}")


def git(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, FileNotFoundError):
        return 127, ""


def is_git_repo() -> bool:
    code, out = git("rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip().startswith("true")


def files_git_would_publish(include_untracked: bool) -> list[str]:
    """返回将要公开的相对路径列表(仓库已 init 时用 git 的答案)。"""
    if not is_git_repo():
        return []
    args = ["ls-files"] if not include_untracked else ["ls-files", "--cached", "--others",
                                                       "--exclude-standard"]
    code, out = git(*args)
    if code != 0:
        return []
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def files_by_walking() -> list[str]:
    """没 init 仓库时的退路:按 .gitignore 里的关键条目手工排除。"""
    ignored_dirs = {".venv", "dist", "shots", "__pycache__", ".tmp", "memory", ".git"}
    ignored_files = {"config.json", "pet.log", "pet-fatal.log", "pet.lock", "pet.lock.pid"}
    result: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        parts = set(rel.split("/")[:-1])
        if parts & ignored_dirs or path.name in ignored_files:
            continue
        if rel.startswith("build/work/") or rel.startswith("assets/model/"):
            continue
        if path.suffix in {".pyc", ".zip"}:
            continue
        result.append(rel)
    return sorted(result)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="开源发布前自检")
    parser.add_argument("--all", action="store_true",
                        help="连还没 add 的新文件一起审(建议在 git add 之后用)")
    args = parser.parse_args()

    in_repo = is_git_repo()
    listing = files_git_would_publish(args.all) if in_repo else files_by_walking()
    source = ("git " + ("ls-files --cached --others --exclude-standard" if args.all
                        else "ls-files")) if in_repo else "目录遍历(尚未 git init)"

    print(f"审计范围:{source}")
    print(f"将公开 {len(listing)} 个文件")

    print("\n[1] 不该公开的东西有没有混进去")
    leaked = [p for p in listing if p.startswith(FORBIDDEN_PREFIXES)]
    check("无运行时产物 / 个人数据 / 第三方模型", not leaked,
          "混进来了:" + "、".join(leaked[:5]) if leaked else "")

    print("\n[2] 必备的开源文件")
    for rel in REQUIRED:
        check(f"{rel} 存在", (ROOT / rel).exists(), "缺失")

    print("\n[3] 内容里的 Key 与本机绝对路径")
    home = Path(os.path.expanduser("~"))
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    #: 用「运行时的真实信息」拼出该被发现的字符串,不要把某个人的路径写死在脚本里
    needles: list[tuple[str, str]] = []
    if user:
        needles.append((f"本机用户名({user})", user))
    if user and home.name:
        needles.append(("本机用户目录", str(home)))
    needles.append(("本仓库绝对路径", str(ROOT)))
    needles.append(("DSH 工作区路径", str(ROOT.parent)))

    secret_hits: list[str] = []
    path_hits: list[str] = []
    for rel in listing:
        path = ROOT / rel
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in KEY_PATTERNS:
            if pattern.search(text):
                secret_hits.append(f"{rel} 命中 {pattern.pattern[:28]}…")
        for label, needle in needles:
            if needle and len(needle) > 3 and needle in text:
                path_hits.append(f"{rel} 含{label}")
    check("没有疑似 API Key", not secret_hits, "; ".join(secret_hits[:3]))
    check("没有本机绝对路径/用户名", not path_hits, "; ".join(path_hits[:4]))

    print("\n[4] 体积与文件数(仓库要让人愿意 clone)")
    sizes = [(p, (ROOT / p).stat().st_size) for p in listing if (ROOT / p).is_file()]
    total_mb = sum(s for _, s in sizes) / 1024 / 1024
    big = [(p, s / 1024 / 1024) for p, s in sizes if s / 1024 / 1024 >= MAX_FILE_MB]
    huge = [(p, s / 1024 / 1024) for p, s in sizes if s / 1024 / 1024 >= HARD_LIMIT_MB]
    check(f"总大小 {total_mb:.1f} MB", total_mb < 30, f"{total_mb:.1f} MB 偏大")
    check("没有超过 50 MB 的文件(GitHub 会拒绝)", not huge,
          "、".join(f"{p}({s:.0f}MB)" for p, s in huge))
    check(f"没有超过 {MAX_FILE_MB:.0f} MB 的文件", not big,
          "、".join(f"{p}({s:.1f}MB)" for p, s in big), warn_only=True)

    print("\n[5] 模型是不是被正确地挡在仓库外")
    model_dir = ROOT / "assets" / "model"
    model_files = [p for p in listing if p.startswith("assets/model/")]
    check("仓库清单里没有模型文件", not model_files, f"{len(model_files)} 个")
    if model_dir.is_dir():
        if in_repo:
            code, out = git("check-ignore", "-q", "assets/model")
            check("assets/model 被 .gitignore 忽略", code == 0, out.strip())
        else:
            ignored = "assets/model/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
            check("assets/model 写在 .gitignore 里", ignored)
        check("本机模型文件齐全(自己跑得起来)", any(model_dir.glob("*.model3.json")),
              "本机也没有模型,运行会提示放置路径")
    else:
        check("本机没有模型目录(用 --model-dir 或按 assets/README.md 放置)",
              True, warn_only=True)

    print("\n[6] 打包脚本与分享包里的许可")
    check("build/build.ps1 会放 LGPL 全文进分享包",
          "LGPL" in (ROOT / "build" / "build.ps1").read_text(encoding="utf-8"))
    share = ROOT / "build" / "share"
    check("分享包里备好 LGPL-3.0.txt", (share / "LGPL-3.0.txt").is_file())
    check("分享包里备好分享说明", any(share.glob("*说明*.txt")), str(list(share.glob("*.txt"))))

    print("\n[7] Windows 脚本的编码陷阱(.ps1 / .iss 必须带 UTF-8 BOM)")
    for rel in listing:
        if not rel.endswith((".ps1", ".iss")):
            continue
        head = (ROOT / rel).read_bytes()[:3]
        check(f"{rel} 带 UTF-8 BOM", head == b"\xef\xbb\xbf",
              "缺 BOM:Windows PowerShell 5.1 会按 ANSI 读 → 中文字符串乱码 → 脚本语法直接坏掉"
              "(实测踩过:补 BOM 即可)")

    print(f"\n=== 通过 {_results['pass']} 项,注意 {_results['warn']} 项,失败 {_results['fail']} 项 ===")
    if _results["fail"]:
        print("❌ 先处理上面的 [FAIL] 再发布。")
    else:
        print("✅ 可以发布。" + ("(有 [注意] 项,自己判断)" if _results["warn"] else ""))
    return 1 if _results["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

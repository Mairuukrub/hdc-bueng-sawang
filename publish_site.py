"""
Publish site/ to GitHub Pages (branch gh-pages of Mairuukrub/hdc-bueng-sawang).

HDC can't be reached from GitHub's runners (connections from abroad time out), so the data
is fetched on a machine in Thailand and only the finished static site is pushed. Each publish
is a single orphan commit force-pushed to gh-pages, so the branch never accumulates history.
Credentials come from the gh CLI's stored login (`gh auth git-credential`).
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(HERE, "site")
STAMP = os.path.join(HERE, "logs", "published.sha256")
REPO_URL = "https://github.com/Mairuukrub/hdc-bueng-sawang.git"
BRANCH = "gh-pages"
GH = os.environ.get("GH_BIN") or shutil.which("gh") or os.path.expanduser("~/.local/bin/gh")
FILES = ["index.html", "app.js", "style.css", "data.js"]


def digest():
    h = hashlib.sha256()
    for name in FILES:
        with open(os.path.join(SITE_DIR, name), "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def publish(force=False):
    """Push the current site if it changed since the last publish. Returns a short status."""
    current = digest()
    if not force and os.path.exists(STAMP) and open(STAMP).read().strip() == current:
        return "unchanged"
    git = ["git", "-c", "credential.helper=", "-c", f"credential.helper=!{GH} auth git-credential",
           "-c", "user.name=Mairuukrub", "-c", "user.email=143485803+Mairuukrub@users.noreply.github.com"]
    with tempfile.TemporaryDirectory() as tmp:
        for name in FILES:
            shutil.copy2(os.path.join(SITE_DIR, name), tmp)
        open(os.path.join(tmp, ".nojekyll"), "w").close()   # serve files as-is
        run = lambda *args: subprocess.run(git + list(args), cwd=tmp, check=True, capture_output=True, text=True)
        run("init", "-q")
        run("checkout", "-q", "--orphan", BRANCH)
        run("add", "-A")
        run("commit", "-q", "-m", "อัปเดตหน้าเว็บและข้อมูล HDC")
        run("push", "-q", "--force", REPO_URL, f"HEAD:{BRANCH}")
    os.makedirs(os.path.dirname(STAMP), exist_ok=True)
    with open(STAMP, "w") as f:
        f.write(current)
    return "published"


if __name__ == "__main__":
    print(publish(force="--force" in sys.argv))

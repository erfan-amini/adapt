"""Publish files to the GitHub repository that Streamlit Cloud deploys from.

One commit per publish, built with the Git Data API (blobs -> tree -> commit
-> ref), so a whole bundle lands atomically. Configuration comes from
Streamlit secrets:

    [github]
    token  = "ghp_..."            # fine-grained token with contents: read/write
    repo   = "erfan-amini/adapt"
    branch = "main"

Binary files (xlsx) are uploaded as base64 blobs; text files as utf-8.
"""
import base64
import os

import requests
import streamlit as st

API = "https://api.github.com"


def github_config():
    cfg = {"token": None, "repo": None, "branch": "main"}
    try:
        from adapt_core.ui import secrets_available
        if not secrets_available():
            raise FileNotFoundError
        if "github" in st.secrets:
            g = st.secrets["github"]
            cfg["token"] = g.get("token")
            cfg["repo"] = g.get("repo", cfg["repo"])
            cfg["branch"] = g.get("branch", cfg["branch"])
    except Exception:
        pass
    cfg["token"] = cfg["token"] or os.environ.get("GITHUB_TOKEN")
    cfg["repo"] = cfg["repo"] or os.environ.get("GITHUB_REPO")
    cfg["branch"] = os.environ.get("GITHUB_BRANCH", cfg["branch"])
    cfg["ready"] = bool(cfg["token"] and cfg["repo"])
    return cfg


def _hdr(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def publish_files(files, message, cfg=None, progress=None):
    """files: {repo_path: bytes}. Returns (commit_sha, html_url)."""
    cfg = cfg or github_config()
    if not cfg["ready"]:
        raise RuntimeError("GitHub token and repository are not configured in Streamlit secrets.")
    tok, repo, branch = cfg["token"], cfg["repo"], cfg["branch"]
    h = _hdr(tok)

    r = requests.get(f"{API}/repos/{repo}/git/ref/heads/{branch}", headers=h, timeout=30)
    r.raise_for_status()
    head_sha = r.json()["object"]["sha"]
    r = requests.get(f"{API}/repos/{repo}/git/commits/{head_sha}", headers=h, timeout=30)
    r.raise_for_status()
    base_tree = r.json()["tree"]["sha"]

    tree = []
    n = len(files)
    for i, (path, data) in enumerate(files.items()):
        if progress:
            progress(i, n, f"Uploading {path}")
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            content = data.decode("utf-8")
            payload = {"content": content, "encoding": "utf-8"}
        except UnicodeDecodeError:
            payload = {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"}
        r = requests.post(f"{API}/repos/{repo}/git/blobs", headers=h, json=payload, timeout=300)
        r.raise_for_status()
        tree.append({"path": path.replace(os.sep, "/"), "mode": "100644", "type": "blob", "sha": r.json()["sha"]})

    r = requests.post(f"{API}/repos/{repo}/git/trees", headers=h,
                      json={"base_tree": base_tree, "tree": tree}, timeout=120)
    r.raise_for_status()
    tree_sha = r.json()["sha"]
    r = requests.post(f"{API}/repos/{repo}/git/commits", headers=h,
                      json={"message": message, "tree": tree_sha, "parents": [head_sha]}, timeout=60)
    r.raise_for_status()
    commit = r.json()
    r = requests.patch(f"{API}/repos/{repo}/git/refs/heads/{branch}", headers=h,
                       json={"sha": commit["sha"], "force": False}, timeout=60)
    r.raise_for_status()
    if progress:
        progress(n, n, "Done")
    return commit["sha"], commit.get("html_url", f"https://github.com/{repo}/commit/{commit['sha']}")

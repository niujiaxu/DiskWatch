# 发布到 GitHub 的步骤

仓库本地已整理好。在 GitHub 上建空仓库后，把下面的地址换成你的：

```bat
cd C:\Code\cursorWork\something
git remote add origin https://github.com/<你的用户名>/DiskWatch.git
git branch -M main
git push -u origin main
```

## 发布 Release

1. GitHub → **Releases** → **Draft a new release**
2. Tag：`v1.0.0`
3. 标题：`DiskWatch 1.0.0`
4. 说明可直接粘贴 `CHANGELOG.md` 里对应段落
5. 附件：上传 `release\DiskWatch-1.0.0-src.zip`（由 `scripts\make_release.ps1` 生成）

## 仓库建议设置

- Description：`Windows desktop widget that tracks newly created files each day`
- Topics：`windows` `filesystem` `watchdog` `pyside6` `desktop-widget` `sqlite`
- 勾选 MIT License（本仓库已有 `LICENSE` 文件）

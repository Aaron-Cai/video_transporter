# Repository Instructions

## README maintenance

- Any change to project README content must update both `readme.md` and `README.zh-CN.md` in the same task.
- Keep the Chinese and English README files aligned in structure and meaning.

## Commit workflow

- If the user asks with a short commit-style command such as `提交一下`, `帮我提交`, `提交代码`, or `一键提交`, treat that as approval to create a git commit for the current task.
- Before committing, review the working tree and stage only files relevant to the current task. Do not include unrelated user changes unless the user explicitly asks for that.
- Write every commit message in English.
- Format commit messages as `type(scope): summary`.
- Restrict `type` to this fixed set: `feat`, `fix`, `refactor`, `docs`, `chore`, `style`.
- Use these scopes for normal project work: `frontend`, `backend`, `config`.
- Include every affected scope in the commit message instead of picking only one primary scope.
- When multiple scopes are involved, join them with commas, for example: `feat(frontend,backend): add video playback flow`.
- Keep the summary concise and specific, for example: `feat(frontend): add browser playback links for downloads`.
- Do not push to the remote unless the user explicitly asks to push.

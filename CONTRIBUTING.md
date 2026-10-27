# Working on Pivot

- **Branch from `main`.** Commit freely; push only when the owner says so.
- **`production-live` is production.** The VM follows it within a minute
  (`deploy/README.md`). Moving it is deploying, moving it back is rolling back,
  and every push to it needs the owner's go-ahead.
- **Pull requests into `main`** run `.github/workflows/pr-validation.yml`:
  ruff, an import check of `backend.main` and pytest collection in `api/`,
  then typecheck and lint in `app/`. No live infrastructure is touched.
- **Before an `app/` or `site/` change reaches `production-live`,** run
  `npx eslint app components lib --quiet` and a clean `next build`. The VM
  builds with lint on, and a failed build is a failed deploy.
- **Chart edits:** bump the `?v=N` stamp in `chart/index.html` for every
  edited file under `chart/js` or `chart/css`.
- **Shared stores:** read `docs/DATA_MAP.md` before touching a database. Two of
  them fail silently when misused.

# PublicOpinionForecast

**舆情预测：可验证、可追踪、可持续更新的多智能体研究系统。**

An event-centred public opinion forecasting research application, extended from [MiroFish](https://github.com/666ghj/MiroFish). The original authorship and AGPL-3.0 license are preserved.

## What is included

- **Forecast verification:** immutable predictions, time-aligned observations, MAE/RMSE/WAPE and same-information baselines.
- **Cost and runtime controls:** preparation reuse, parallel configuration, request/token accounting, configurable pricing and budgets, and simulation windows aligned with the research horizon.
- **Adaptive forecasting:** causal error feedback, bounded residual candidates and baseline fallback, immutable rolling versions, and explicit historical versus prospective evidence.
- **One event, one workflow:** materials, native graph/agent simulation, frozen predictions, observations, rolling updates and consolidated reports stay associated without copying internal identifiers.
- **Delivery:** environment checks, deterministic offline demonstrations, source-backed historical samples, Markdown/HTML/JSON reports, persistent event records and local Docker packaging.

## Run locally

Use Node.js 22.12+ and Python 3.11/3.12. The existing lock files are retained.

```powershell
npm ci
npm ci --prefix frontend
cd backend
uv sync --frozen --python 3.11 --no-editable
cd ..
npm run doctor
npm run dev:research
```

Open **http://127.0.0.1:3000/**. The event workspace is the main entry. Choose **体验完整合成流程** to test the entire local workflow without model keys or paid API requests.

For live multi-agent simulations, configure the existing model and Zep settings in a local `.env`, then run `npm run dev`. Do not commit credentials. Model pricing and request limits belong in the runtime settings.

## Local Docker delivery

```powershell
docker compose up --build
```

Open **http://127.0.0.1:5001/**. Compose builds this checkout, including the modifications. It does not run the upstream MiroFish image. Local research mode is enabled by default; observations and reports persist in `backend/uploads/`.

To enable live simulations later, set `MIROFISH_RESEARCH_ONLY=0` and provide model/Zep settings in `.env`, then recreate the service. This package is intended for a local research deployment; it is not a hosted multi-tenant service.

## Verify and reproduce

```powershell
npm run verify
```

The verification workflow runs backend business tests, the frontend production build, and a packaged-app smoke test in CI. Live model quality is a separate acceptance step, requiring credentials and an explicit experiment budget.

Five anonymous PHEME conversation-count samples and a reproducible historical numeric replay are included in `docs/datasets/pheme/`. They are not evidence that live MiroFish simulations outperform simple baselines. Full results, including unsuccessful comparisons, are retained.

## Documentation

- [完整事件流程、部署与验收](docs/项目整体闭环与交付.md)
- [滚动预测与真实事件操作手册](docs/闭环验收与真实事件运行手册.md)
- [预测验证与降本说明](docs/研究验证与降本说明.md)
- [真实历史样本及来源](docs/datasets/pheme/README.md)
- [历史数量回放结果](docs/benchmarks/pheme-rolling.json)
- [上游 MiroFish](https://github.com/666ghj/MiroFish)

## Attribution and license

Based on `666ghj/MiroFish`, original baseline commit `7657031ac01184afe2cb220f5ee3545573b5e843`. PublicOpinionForecast adds research verification, runtime accounting, adaptive numeric prediction and an integrated event/report workflow. Original source documentation is archived under `docs/upstream/`.

Released under **AGPL-3.0**, as required by the upstream license. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md). Research functionality and test results do not guarantee future event outcomes.

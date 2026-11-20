# EDA 자동화 MCP — 데이터셋 · 시각화 도구 조사

조사일: 2026-11-20

## 1. 예시 데이터셋 (`data/`에 CSV로 저장)

Kaggle CLI/인증이 없어 Kaggle에서 직접 받지는 못했다. 같은 데이터의 원출처(UCI, Zenodo, GitHub)에서 인증 없이 받았다.

| # | 데이터셋 | 파일 | 크기 (행 × 열) | 타깃 | 라이선스 | EDA 관점의 특징 |
|---|---|---|---|---|---|---|
| 1 | **UCI SECOM** | `data/secom/secom.csv` | 1,567 × 592 | `label` (-1 양품 1,463 / 1 불량 104) | CC BY 4.0 | 넓은 테이블(센서 590개), 결측 4.5%, 불균형 14:1, 상수 컬럼 다수 |
| 2 | **Infineon 장비 센서** (Zenodo 4282611) | `data/infineon_equipment/equipment1.csv`<br>`equipment2.csv`<br>`response.csv` | 170,896 × 27<br>232,144 × 35<br>2,638 × 4 | `response`(수치), `class` (good 2,110 / bad 528) | CC BY 4.0 | lot·wafer별 시계열(웨이퍼당 176 시점), 장비 2대, 테이블 조인 필요 |
| 3 | **Wafer fault 샘플** | `data/wafer_fault/wafer_23012020_041211.csv` | 100 × 592 | `Good/Bad` (-1 94 / 1 6) | 명시 없음 | 작은 스모크 테스트용. SECOM과 같은 구조(센서 590개) |

### 저장하면서 바꾼 것

- **SECOM**: 원본은 공백 구분 `secom.data` + `secom_labels.data` 두 파일. 하나로 합쳐 `label, timestamp, f0..f589` 헤더를 붙였다. 원본에 컬럼명이 없어 `f0..` 은 임의로 붙인 이름이다. 컬럼 설명은 `secom.names` 참고.
- **Infineon**: 원본은 `;` 구분 + Latin-1 인코딩. 쉼표 구분 + UTF-8로 다시 썼다. 값은 건드리지 않았다. 컬럼 설명은 `DataSetDescription.txt` 참고.
- **Wafer fault**: 원본 그대로.

### 주의: Infineon 원본의 값 손상

원본 CSV 자체에 엑셀 날짜 자동변환으로 깨진 값이 들어 있다 (`19.Mai`, `Okt.96`, `Sep.71` 등 독일어 월 이름).

- `equipment1.csv`: `sensor_3` 1개 컬럼, 6개 셀
- `equipment2.csv`: 9개 컬럼(`sensor_26, 31, 32, 37, 38, 39, 40, 54, 55`), 119,576개 셀

이 컬럼들은 pandas에서 문자열(object)로 읽힌다. 고치지 않고 그대로 뒀다 — EDA 도구가 "수치여야 할 컬럼에 문자열이 섞임"을 잡아내는지 보는 테스트 케이스로 쓸 수 있다. 원값 복원은 불확실하다(`19.Mai` → 19.05 로 추정 가능하지만 `Okt.96` 은 10.96 인지 10.1996 인지 모호).

### 받지 않은 후보

- **WM-811K 웨이퍼 맵** (Kaggle `qingyi/wm811k-wafer-map`): 81만 장 웨이퍼 불량 패턴. pickle 형식의 2D 맵이라 CSV 테이블 EDA에는 맞지 않아 제외.
- **Zenodo 4462777** (Infineon 요약 통계 버전, 114 × 77): 2번의 가공본이라 제외.
- Kaggle 미러(검색 결과 기준, 직접 확인 못 함): `paresh2047/uci-semcom`(SECOM), `bhushanbhawarkar/wafer-dataset`.

## 2. 시각화 도구 선정

### 선정: **Plotly (plotly.py)** — MIT

| 기준 | Plotly | Vega-Lite / Altair | Matplotlib / Seaborn | AntV mcp-server-chart |
|---|---|---|---|---|
| 라이선스 | MIT | BSD-3 | PSF 계열 / BSD-3 | MIT |
| pandas 연동 | `plotly.express`로 한 줄 | 좋음 | 좋음 | 없음 (Node, JSON 입력) |
| 에이전트용 출력 | JSON spec + HTML + PNG | JSON spec + PNG | PNG만 | 이미지 URL |
| 큰 데이터 (40만 행) | `scattergl`(WebGL)로 가능 | 데이터가 spec에 박혀 무거움, 기본 5,000행 제한 | 가능 | 부적합 |
| 인터랙션 (줌, 호버) | 있음 | 있음 | 없음 | 제한적 |
| 반도체 EDA 차트 | 히트맵, 박스, 시계열, SPC 관리도, 웨이퍼 맵(heatmap) 모두 기본 제공 | 대부분 가능 | 모두 가능 | 일반 비즈니스 차트 위주 |

선정 이유:

1. **한 번 그려서 두 군데 쓴다.** 같은 figure를 PNG로 내보내 에이전트(모델)가 보게 하고, HTML로 내보내 사람이 줌·호버하며 본다. Matplotlib은 PNG만 된다.
2. **figure가 JSON이다.** MCP 도구 결과로 그대로 직렬화되고, 에이전트가 나중에 수정 요청하기 쉽다.
3. **데이터 규모가 맞는다.** Infineon 시계열(행 40만)을 WebGL로 그린다. Vega-Lite는 데이터를 spec에 통째로 넣어서 이 크기에 불리하다.
4. **Python MCP 서버 안에서 pandas와 같은 프로세스로 돈다.** 별도 Node 서버가 필요 없다.

알아둘 제약:

- **PNG 내보내기에 Chrome이 필요하다.** `kaleido` v1부터 Chromium을 번들하지 않는다. 설치: `pip install plotly kaleido` 후 `plotly_get_chrome`. 서버 환경에 Chrome을 못 넣으면 Altair + `vl-convert-python`(브라우저 없이 PNG 렌더)이 대안이다.
- SECOM처럼 컬럼이 590개면 상관 히트맵을 전부 그리지 말고, 결측·분산으로 걸러 상위 N개만 그리게 MCP 도구에서 제한해야 한다.

### 참고할 기존 MCP 서버 (검색 결과 기준, 직접 실행해 보지 않음)

- `isaacwasserman/mcp-vegalite-server` — `save_data` / `visualize_data` 두 도구로 된 최소 구조. 도구 설계 참고용. 라이선스 미확인.
- `xoniks/mcp-visualization-duckdb` — DuckDB 쿼리 + Plotly 렌더. 이번 선정과 가장 가까운 구성.
- `antvis/mcp-server-chart` — 가장 널리 쓰이는 차트 MCP. 26종 이상 차트.

## 3. 출처

- UCI SECOM: https://archive.ics.uci.edu/dataset/179/secom
- Infineon 장비 센서 데이터: https://zenodo.org/record/4282611
- Infineon 요약 통계 버전: https://zenodo.org/record/4462777
- Wafer fault 샘플: https://github.com/Monish-Nallagondalla/sensor_fault_detection
- 시각화 MCP 서버 리뷰: https://dev.to/grove_chatforest/data-visualization-mcp-servers-antv-echarts-vega-lite-plotly-matplotlib-d3-and-more-7k7
- mcp-vegalite-server: https://mcpservers.org/servers/isaacwasserman/mcp-vegalite-server

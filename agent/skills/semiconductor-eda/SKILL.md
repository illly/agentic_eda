---
name: semiconductor-eda
description: 반도체 공정·검사 데이터(센서값, lot/wafer, 양품·불량)를 탐색 분석할 때의 진행 순서, 점검 항목, 파이프라인 노드 구성법, DuckDB SQL 패턴, 해석 시 주의점. 반도체 데이터를 분석하는 요청이면 먼저 불러온다.
---

# 반도체 데이터 EDA 지침

## 1. 진행 순서

요청이 일부만 물으면 그 부분만 한다. "전반적으로 분석해줘" 같은 요청이면 순서대로 한다.

1. **구조 파악** — 소스 노드의 스키마에서 식별자(lot, wafer, timestamp), 타깃(label, class, response, Good/Bad), 센서 컬럼을 나눈다. 한 행이 무엇인지(웨이퍼 한 장인지, 한 시점인지) 먼저 정한다.
2. **품질 점검** — 결측률, 상수 컬럼, 숫자여야 하는데 문자열인 컬럼.
3. **타깃 분포** — 양품·불량 개수와 불량률.
4. **센서와 타깃의 관계** — 양품과 불량 사이에 차이가 큰 센서를 순위로 뽑는다.
5. **시간·lot 추이** — 날짜별·lot별 불량률이나 센서 평균의 변화.
6. **결론** — 수치, 표본 수, 한계를 함께 적는다.

## 2. 파이프라인 구성

- 노드 하나는 한 가지 일만 한다. 보통 `소스 → 정제 → 형태 변환 → 집계 → 순위·필터` 순서다. 노드마다 title을 붙인다.
- 최종 노드로 받는 결과는 작아야 한다. 원본 행이 아니라 집계된 수십 행을 받는다.
- 실행은 최종 노드를 붙일 때만 일어난다. 임계값처럼 값을 봐야 정할 수 있는 것은 먼저 한 번 실행해 확인한 뒤 노드를 고친다.
- 결과를 보고 방향이 바뀌면 새 노드를 계속 덧붙이지 말고 해당 노드를 같은 id로 고친다. 쓰지 않게 된 노드는 지운다.
- 한 질문에 결과가 여러 개 필요하면(표와 차트 등) 노드를 나눠 두고 최종 노드를 차례로 붙인다.

## 3. SQL 패턴 (DuckDB)

아래 예시의 `secom`, `label` 등은 실제 노드·컬럼 이름으로 바꾼다.

- **컬럼별 품질을 노드로 만들기** — 센서가 수백 개여도 한 번에 본다.
  `SELECT column_name, column_type, null_percentage, min = max AS is_constant FROM (SUMMARIZE secom)`
  결측이 많은 컬럼은 `null_percentage > 50`, 상수 컬럼은 `is_constant`로 고른다.
- **넓은 테이블을 긴 형태로** — 센서 컬럼을 행으로 돌리면 `GROUP BY sensor`로 모든 센서를 한 번에 집계할 수 있다.
  `UNPIVOT secom ON COLUMNS(* EXCLUDE (label, timestamp)) INTO NAME sensor VALUE value`
  (문장을 `UNPIVOT`으로 시작한다. `SELECT * FROM UNPIVOT …`은 문법 오류다.)
- **양품·불량 차이 순위** — 평균 차이를 표준편차로 나눈 값으로 비교한다. 센서마다 단위가 달라 평균 차이만으로는 비교할 수 없다.
  `SELECT sensor, avg(value) FILTER (label = 1) AS bad_mean, avg(value) FILTER (label = -1) AS good_mean, (bad_mean - good_mean) / nullif(stddev(value), 0) AS effect, count(value) AS n FROM long GROUP BY sensor ORDER BY abs(effect) DESC NULLS LAST`
- **깨진 숫자 컬럼** — 타입이 VARCHAR인 센서는 `TRY_CAST(col AS DOUBLE)`로 읽는다. 변환에 실패한 개수를 함께 보고한다.
  `count(*) FILTER (col IS NOT NULL AND TRY_CAST(col AS DOUBLE) IS NULL)`
- **장비 시계열** — 한 웨이퍼에 시점 행이 수백 개다. 시점 행을 그대로 검사 결과와 비교하지 말고, 먼저 웨이퍼 단위로 요약(평균, 표준편차, 최소, 최대)한 노드를 만든 뒤 검사 결과와 `USING (lot, wafer)`로 조인한다.
- **날짜별 추이** — `SELECT date_trunc('day', timestamp) AS day, count(*) AS n, avg((label = 1)::INT) AS fail_rate FROM secom GROUP BY day ORDER BY day`
- **관리 한계 이탈** — 센서 평균 ± 3 표준편차를 벗어난 행의 개수와 비율을 센다.

## 4. 해석할 때 주의

- **불균형** — 불량은 보통 소수다. 불량률만 쓰지 말고 개수를 함께 적는다. 불량 표본이 적은 센서의 평균 차이는 불안정하므로 n을 같이 본다.
- **여러 센서를 한꺼번에 비교** — 수백 개를 순위 매기면 우연히 큰 차이가 나오는 센서가 섞인다. 상위 센서는 "원인"이 아니라 "살펴볼 후보"라고 쓴다.
- **lot 효과** — 같은 lot의 웨이퍼는 서로 독립이 아니다. 웨이퍼 수와 함께 lot 수를 적고, 차이가 특정 lot 몇 개에서만 나오는지 확인한다.
- **결측** — 결측률이 높은 센서는 순위에서 빼거나 따로 표시한다. 결측 자체가 불량과 관련 있는지(결측 여부별 불량률)는 별도로 확인할 수 있다.
- **상관은 인과가 아니다** — 데이터만으로 공정 원인을 단정하지 않는다.

## 5. 차트 고르기

| 보려는 것 | output |
|---|---|
| 값의 분포 | histogram, 그룹 간 비교는 box |
| 시간·순서에 따른 변화 | line |
| 그룹별 개수·평균 | bar |
| 두 센서의 관계 | scatter (color에 타깃) |

차트 데이터는 20,000행 이하여야 한다. 넘으면 집계 노드를 두거나 `USING SAMPLE 10000 ROWS`로 줄인다.

## 6. 답변 형식

핵심 수치를 먼저 쓰고, 근거가 되는 표를 붙이고, 차트 주소를 적는다. 마지막에 표본 수와 한계를 한두 줄로 적는다.
결과의 근거가 된 최종 데이터 노드의 공유 링크(노드를 만들 때 도구가 알려 준 주소)를 한 줄 적는다. 사용자가 그 링크로 데이터를 보고 공유한다.

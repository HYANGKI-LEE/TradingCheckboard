# 채권/IRS 트레이딩 대시보드

인포맥스에서 뽑은 채권시장 데이터를 스프레드 거래/환거래 관점에서 볼 수 있는 대시보드.

## 데이터 업데이트 흐름 (매일 자동)

Windows 작업 스케줄러 `TradingCheckboard_DailyUpdate` 가 평일 07:40에 `scripts/daily_update.py` 를 실행한다.

1. **RawData_master.xlsx 업데이트** - `scripts/refresh_master.ps1` 이 마스터 복사본에서 인포맥스 IMDH 를 재조회하고
   (DB에서 비어 있는 구간부터 시작일(B1)을 자동 조정), **전 영업일 날짜가 시트별로 채워졌는지 확인**한 뒤 마스터를 교체
2. **market.db 업데이트** - 마스터의 값을 DB에 반영 (엑셀에 값이 있는 칸만 덮어쓰고, 없는 날짜는 추가, 엑셀에 없는 칼럼은 유지)

조건: PC가 켜져 있거나 절전모드, 윈도우 로그인 상태, **인포맥스 로그인 상태**. 실패하면 10분 간격으로 최대 12번(07:40~09:30) 재시도하고,
끝까지 실패하면 마스터/DB를 건드리지 않는다. 로그는 `scripts/logs/daily_YYYYMMDD.log`.

3. **배포** - `market_recent.db`(기준일 이후분, 수 MB 이하)를 다시 만들고 GitHub Pages 정적 사이트(`docs/index.html`)도 DB 기준으로
   다시 생성해서 GitHub 에 푸시 -> Streamlit Cloud 자동 재배포 + Pages 갱신(푸시 1회 증가분은 1MB 안팎).
   과거분 `market_history.db`(약 74MB)는 한 번만 올려두고 매일 올리지 않는다. 앱은 두 파일을 합쳐 읽고 같은 날짜는 recent 가 우선.
   (`market.db`(로컬 전체본)가 있으면 그것만 읽는다 - 로컬 개발용, 깃 미추적)

수동 실행: `python scripts/daily_update.py --force` (주말 포함) / 엑셀을 직접 갱신했으면 `--skip-excel` (확인 + DB 업데이트만) / 푸시 없이 `--no-push` / 정적 사이트 생략 `--no-static`.
history 를 다시 만들 때(과거 값 정정 등, 드물게): `python scripts/export_deploy_db.py --history-cutoff YYYY-MM-DD --rebuild-history` 후 history 파일을 커밋.

## 데이터 구조 (`data/market.db`, 대시보드가 읽는 파일)

- `market` : 날짜 1행 x 시리즈 N칼럼(`<시트>__<시리즈명>`) 통합 와이드 테이블 (1990~)
- `series` : 칼럼별 시트/블록제목/필드, `meta` : 빌드 정보
- `data_loader.py` 가 series 의 (블록제목, 필드)를 (그룹, 만기)로 매핑해서 long-format DataFrame 으로 돌려준다
  (시리즈를 추가/삭제해도 블록 제목/필드 규칙만 지키면 그대로 동작)
- 최초 구축/전체 재생성: `python scripts/build_sqlite_db.py --src <히스토리 xlsx> --update <마스터 xlsx>`,
  특정 시트만 수동 반영: `python scripts/update_sqlite_db.py --sheet "Info(원자재)" [--drop-missing]`

## 로컬 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 현재 범위

- 🏛️ 국고채/통안채 금리커브 (스냅샷 + 날짜 비교 + 만기별 히스토리)
- 🏦 신용스프레드 (은행채/카드채/기타금융채/회사채 등급별 vs 국고채·통안채, bp + 등급 사다리)
- 🔁 IRS 커브 / 본드스왑 스프레드 (IRS-국고채, bp)
- 📉 단기금리 (기준금리, CD 91일)

추후 CRS/FX스왑포인트/재정거래 등은 관련 인포맥스 데이터가 추가되면 확장 예정.

## 마스터 엑셀 편집 규칙

- **원본은 `data/RawData_master.xlsx` 하나**: `RawData_master.prev.xlsx`는 매일 루틴이 만드는 전날 자동 백업, `RawData_master.new.xlsx`는
  루틴 실행 때 마스터가 엑셀에서 열려 있을 때만 생기는 임시본이다. 퇴근 전에는 마스터를 닫아 둔다.
- **데이터 시트 이름은 `Info(…)` 형식**: 루틴은 `Info(` 로 시작하는 시트만 처리한다(메모/계산용 시트는 무시되고 값도 안 건드림).
  시트는 1행 설정, 2행 IMDH 제목, 3행 필드명, 4행 조회실패 표시, 5행부터 날짜 순서의 레이아웃을 지켜야 한다.
- **새 블록 추가**: 해당 종류의 시트 오른쪽 끝에 IMDH 블록을 추가하면 DB 칼럼이 이름(`<시트>__<블록제목>-<필드>`)으로 자동 추가된다.
  루틴이 시작일(B1)을 최근 며칠로 바꿔서 조회하므로 과거 히스토리는 1회 길게 재조회해서 이어 붙여야 한다.
  대시보드에 보이려면 `data_loader.py` 의 (그룹, 만기) 매핑 규칙에 맞아야 한다(주가·원자재·FX·해외금리는 제목만 맞으면 자동).
- **시트 이름 변경은 주의**: DB 칼럼 접두사가 시트 이름에서 만들어져서, 이름을 바꾸면 새 칼럼이 생기고 옛 칼럼은 갱신이 멈춘다
  (대시보드는 옛 칼럼을 계속 읽어 조용히 멈춘 데이터를 보여줌). 바꿔야 하면 DB 칼럼 이전 + `data_loader.py` 시트 상수 수정이 필요하다.
  루틴 로그에 'DB에는 있는데 마스터에 없는 시트' 경고가 남는다.

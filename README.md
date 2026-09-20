# BACnet/IP Controller 자동화 회귀 시험 (Automation Test Suite)

라즈베리파이(또는 Linux Test PC)에서 대상 BACnet/IP 컨트롤러의 오브젝트 탐색, 표준 속성 읽기 검증, 속성 쓰기/원복 검증, Wireshark 패킷 캡처, 인터랙티브 HTML 리포트 생성을 일괄 수행하는 자동화 테스트 도구입니다.

---

## 주요 기능

1. **오브젝트 자동 탐색 및 설정 생성 (`generate_config.py`)**
   - 대상 컨트롤러의 `object-list`를 자동 탐색하여 테스트 대상 YAML 설정 파일 생성
   - AI, AO, AV, BI, BO, BV, MSI, MSO, MSV, IV, PIV, CSV, LAV, Device, Trend Log 등 다양한 프로파일 지원
   - 각 오브젝트의 `object-name`을 자동으로 읽어 직관적인 식별자 부여

2. **속성(Property) 읽기 검증 (`property_read.py`)**
   - 오브젝트별 표준 속성 및 Intrinsic Reporting(알람 관련) 속성 일괄 조회
   - BACnet 표준 규격에 맞춘 예외 처리:
     - `bo` (Binary Output): `alarm-value` 제외 (COMMAND_FAILURE 방식)
     - `trend_log`: `log-buffer` 제외 (ReadRange 전용 속성)
     - `event-message-texts` 제외

3. **속성 쓰기 및 원복 검증 (`property_write.py`)**
   - 대상 속성에 테스트 값을 쓰고, Readback을 통해 실제 적용 여부 검증 후 원래 값으로 **자동 원복(Restore)**
   - **Commandable 포인트 (AO, BO)**: Priority 8에 쓰기 후, 테스트 완료 시 `Null`로 우선순위 자동 해제(Relinquish)
   - **입력 포인트 (AI, BI) Present-Value 쓰기**: `write-access-denied` 발생 시 `out-of-service`를 `True`로 변경 후 쓰기 검증, 완료 후 다시 원래 상태(`False`)로 복구
   - **Multi-State `state-text` 배열 쓰기**: 컨트롤러의 `value-out-of-range` 방지를 위해 배열 1번 인덱스(`state-text[1]`)만 안전하게 쓰기/원복
   - **Binary Present-Value 토글**: `active` ↔ `inactive` 정확한 상태 반전 지원

4. **인터랙티브 HTML 리포트 생성 (`html_reporter.py`)**
   - 테스트 실행 시 JSON 리포트와 함께 브라우저에서 바로 볼 수 있는 단독 HTML 파일 자동 생성
   - 상단 KPI 요약 카드, 성공률 프로그레스 바, 실시간 검색창, 상태 필터 버튼(`All`, `Passed/Writable`, `Failed`), 프로파일 필터 제공
   - 외부 인터넷/CDN 연결 없이 100% 오프라인 동작 (`file://` 직접 열기 가능)

5. **Wireshark 패킷 캡처 자동화 (`run_with_pcap.sh`)**
   - 테스트 실행 중 BACnet 패킷(UDP 47808/47809)을 백그라운드에서 자동 캡처하여 `.pcap` 파일로 저장
   - `tcpdump` sudo 권한 유무를 자동 감지하여 무암호 실행 지원

---

## 프로젝트 파일 구조

```
bacnet-auto-test/
├── generate_config.py          # 대상 컨트롤러 오브젝트 자동 탐색 및 YAML 설정 생성
├── property_read.py            # 표준 속성 읽기 검증 스크립트
├── property_write.py           # 속성 쓰기, Readback 검증 및 안전 원복 스크립트
├── html_reporter.py            # JSON 결과를 인터랙티브 HTML 리포트로 변환
├── run_with_pcap.sh            # tcpdump 패킷 캡처 래퍼 스크립트
├── simple_bacnet_test.py       # 기본 BACnet 통신 점검 스크립트
├── config/
│   ├── property-read.yaml      # 테스트 대상 디바이스 및 오브젝트 설정 파일
│   ├── property-read.example.yaml
│   └── simple-bacnet-test.yaml
├── reports/                    # 테스트 결과 저장 경로 (JSON, HTML, PCAP)
│   ├── property-read.json & .html
│   ├── property-write-result.json & .html
│   └── bacnet_YYYYMMDD_HHMMSS.pcap
└── requirements.txt
```

---

## 설치 및 준비 (라즈베리파이 / Linux PC)

```bash
# 1. 패키지 및 파이썬 가상환경 설정
sudo apt update
sudo apt install -y python3 python3-venv python3-pip tcpdump
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 실행 권한 부여
chmod +x run_with_pcap.sh

# 3. (선택/권장) sudo 없이 tcpdump를 실행할 수 있도록 권한 부여
sudo setcap cap_net_raw,cap_net_admin=eip $(which tcpdump)
# 확인 (인터페이스 목록이 sudo 없이 출력되면 성공):
tcpdump -D
```

---

## 사용 가이드

### 1단계: 컨트롤러 오브젝트 자동 탐색 및 설정 생성

컨트롤러의 IP 주소를 지정하여 자동으로 오브젝트 목록을 읽고 `config/property-read.yaml` 파일을 생성합니다.

```bash
# 전체 오브젝트 탐색
python generate_config.py <CONTROLLER_IP>

# 예시:
python generate_config.py 192.168.219.130

# 옵션: 타입별 1개씩만 빠르게 샘플링하여 생성하고 싶을 때
python generate_config.py 192.168.219.130 --first-per-type
```

---

### 2단계: 속성(Property) 읽기 검증 테스트

생성된 설정을 바탕으로 각 오브젝트의 표준 속성을 읽어 검증합니다.

```bash
# 속성 읽기 테스트 실행
python property_read.py

# 옵션: 타입별 1개씩 빠른 샘플링 테스트
python property_read.py --first-per-type

# 다른 설정 파일 지정 시:
python property_read.py --config config/property-read.yaml
```

* **출력 결과**:
  * 콘솔: 각 속성별 `[PASSED]` / `[FAILED]` 및 현재 값 실시간 출력
  * JSON 리포트: `reports/property-read.json`
  * **HTML 리포트**: `reports/property-read.html`

---

### 3단계: 속성 쓰기(Write) 및 원복(Restore) 검증 테스트

오브젝트들의 주요 속성에 대해 쓰기 권한, Readback 일치 여부, 안전 복구를 검증합니다.

```bash
# 타입별 1개씩 샘플링 스모크 테스트 (빠른 확인 추천)
python property_write.py --first-per-type

# 전체 오브젝트 쓰기 테스트
python property_write.py

# 특정 오브젝트만 테스트
python property_write.py --object-id analog-value,1

# 특정 속성만 테스트
python property_write.py --property high-limit

# 드라이런 (실제 쓰기 요청 없이 테스트할 값 미리보기)
python property_write.py --dry-run
```

* **주요 테스트 대상 속성**:
  * **공통**: `object-name`, `description`
  * **AI, AO, AV, IV, PIV, LAV**: `high-limit`, `low-limit`, `deadband`, `time-delay`, `cov-increment`, `units`, `out-of-service`, `present-value`
  * **BI, BO, BV**: `active-text`, `inactive-text`, `time-delay`, `polarity`(BI/BO), `out-of-service`, `present-value`
  * **MSV, MSI, MSO**: `state-text[1]`, `time-delay`, `present-value`

* **출력 결과**:
  * 콘솔: `[WRITABLE]`, `[READ_ONLY]`, `[NOT_SUPPORTED]`, `[MISMATCH]` 등 상태 표시
  * JSON 리포트: `reports/property-write-result.json`
  * **HTML 리포트**: `reports/property-write-result.html`

---

### 4단계: Wireshark 패킷 캡처와 함께 실행

테스트 실행 중 오가는 BACnet UDP 트래픽을 자동으로 캡처하여 `.pcap` 파일로 저장합니다. 실행하려는 명령어 앞에 `./run_with_pcap.sh`만 붙이면 됩니다.

```bash
# 속성 읽기 테스트 + 패킷 캡처
./run_with_pcap.sh python property_read.py --first-per-type

# 속성 쓰기 테스트 + 패킷 캡처
./run_with_pcap.sh python property_write.py --first-per-type
```

캡처가 완료되면 `reports/bacnet_YYYYMMDD_HHMMSS.pcap` 파일이 자동 생성됩니다.

---

## 리포트 확인 및 분석

### 1. 웹 브라우저에서 HTML 리포트 확인
생성된 HTML 리포트에는 실시간 필터 버튼, 검색창, 프로파일 필터가 내장되어 있어 수천 개의 포인트도 간편하게 분석할 수 있습니다.

윈도우 PC에서 리포트 파일을 가져오려면 PowerShell에서:
```powershell
# HTML 리포트 및 패킷 파일 가져오기
scp pi@<라즈베리파이IP>:/home/pi/bacnet-auto-test/reports/*.html .
scp pi@<라즈베리파이IP>:/home/pi/bacnet-auto-test/reports/*.pcap .

# 브라우저에서 열기
start property-read.html
start property-write-result.html
```

### 2. 기존 JSON 파일을 HTML로 수동 변환
이미 저장된 JSON 파일이 있다면 언제든지 다음 명령어로 HTML을 다시 생성할 수 있습니다:
```bash
python html_reporter.py reports/property-read.json
python html_reporter.py reports/property-write-result.json
```

---

## 쓰기 안전성 원칙 (Safety & Rollback)

1. **자동 원복 (Restore)**:
   - 쓰기 테스트 후 즉시 원래 값(`original_value`)으로 복구 쓰기를 실행합니다.
   - `--no-restore` 옵션을 명시하지 않는 한 모든 변경 사항은 원복됩니다.
2. **Commandable 포인트 (AO, BO) 해제**:
   - BACnet 우선순위(기본값 Priority 8)로 쓰기 검증 후, 원복 시 `Null`을 기록하여 원래 상위 우선순위 또는 Relinquish-Default 상태로 정상 반환합니다.
3. **입력 포인트 (AI, BI) 안전 제어**:
   - 컨트롤러에 따라 Input 객체 쓰기 시 `write-access-denied`가 발생하면, `out-of-service=True`로 전환 후 시험하고 종료 시 반드시 `out-of-service=False`로 원복합니다.

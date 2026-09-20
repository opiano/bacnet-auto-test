# BACnet/IP Controller Regression Test

Ubuntu test PC에서 BACnet/IP Controller의 기본 회귀 시험을 수행하는 Python 프로젝트입니다.

## 포함된 시험

- Who-Is에 대한 대상 Device Instance의 I-Am 응답
- Device Object의 기본 식별 속성 읽기
- 설정된 필수 Object/Property 읽기
- 명시적으로 허용한 테스트 전용 출력 Object의 write/readback
- 존재하지 않는 Object 요청 시 BACnet error 응답

## 빠른 시작

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/controller.example.yaml config/controller.yaml
```

`config/controller.yaml`에서 Test PC의 IP/CIDR, Controller 주소와 Device Instance, 실제 Object instance를 변경합니다. Test PC Device Instance와 Controller Device Instance는 반드시 달라야 합니다.

```bash
source .venv/bin/activate
pytest -v --bacnet-config config/controller.yaml \
  --junitxml=reports/bacnet-result.xml \
  --html=reports/bacnet-report.html
```

## 쓰기 안전성

`safe_write.enabled`의 기본값은 `false`입니다. 실설비 출력이 아닌 테스트 전용 AO/BO/AV/BV를 Controller 펌웨어에 제공한 뒤에만 `true`로 바꾸십시오. `relinquish_after_test: true`는 성공한 write priority를 `Null`로 해제합니다.

## 네트워크 조건

- Test PC와 Controller를 같은 IP subnet의 시험망에 둡니다.
- UDP 47808(BACnet/IP)을 양방향 허용합니다.
- 서로 다른 subnet이라면 BBMD 또는 Foreign Device를 별도로 구성해야 합니다.
- 실패 분석에는 `sudo tshark -i <NIC> -f "udp port 47808" -w reports/failure.pcapng`를 사용합니다.

## CI 실행

펌웨어 build -> Controller download/reboot -> BACnet regression pytest -> JUnit/HTML report 보관 순서로 구성합니다. Controller가 완전히 기동된 후 pytest를 실행해야 합니다.

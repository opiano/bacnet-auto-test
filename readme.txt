# 1. 대상 디바이스에서 오브젝트 리스트를 읽어와 config 생성
python generate_config.py <CONTROLLER_IP>
# 옵션 예시 (특정 인스턴스 지정 또는 타입별 1개씩 샘플링)
# python generate_config.py 192.168.219.227 --first-per-type

# 2. 표준 속성(Property) 자동 읽기 검증 실행 (HTML 리포트 자동 생성)
python property_read.py
# 옵션: 타입별 1개씩 빠른 샘플링 테스트
# python property_read.py --first-per-type
# 결과: reports/property-read.json, reports/property-read.html

# 3. 주요 속성 쓰기(Write) 및 원복(Restore) 검증 테스트 (HTML 리포트 자동 생성)
# 타입별 1개씩 샘플링 테스트 (추천: 빠른 확인)
python property_write.py --first-per-type

# 전체 오브젝트 쓰기 테스트
python property_write.py --config config/property-read.yaml
# 결과: reports/property-write-result.json, reports/property-write-result.html

# 특정 오브젝트나 특정 속성만 테스트
# python property_write.py --object-id analog-value,1
# python property_write.py --property high-limit
# python property_write.py --dry-run   # 쓰기 전 미리보기

# 4. Wireshark 패킷 캡처와 함께 테스트 실행 (자동으로 .pcap 파일 생성)
./run_with_pcap.sh python property_read.py --first-per-type
./run_with_pcap.sh python property_write.py --first-per-type
# Wireshark 분석 팁: 필터창에 'bacnet.text_message' 입력 시 오브젝트별 시작 마커 확인 가능

# 생성된 pcap 파일을 윈도우 PC로 복사 (윈도우 PowerShell에서 실행):
# scp pi@192.168.219.125:/home/pi/bacnet-auto-test/reports/*.pcap .

# 5. 기본 Read/Write 테스트
python simple_bacnet_test.py --config config/simple-bacnet-test.yaml
cat reports/bacnet-simple-result.json

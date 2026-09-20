# 1. 대상 디바이스에서 오브젝트 리스트를 읽어와 config 생성
python generate_mandatory_config.py <CONTROLLER_IP>
# 옵션 예시 (특정 인스턴스 지정 또는 타입별 1개씩 샘플링)
# python generate_mandatory_config.py 192.168.219.227 --first-per-type

# 2. 필수 속성(Mandatory Property) 자동 검증 실행
python mandatory_property_read.py --config config/mandatory-property-read.yaml
cat reports/mandatory-property-read.json

# 3. 기본 Read/Write 테스트
python simple_bacnet_test.py --config config/simple-bacnet-test.yaml
cat reports/bacnet-simple-result.json

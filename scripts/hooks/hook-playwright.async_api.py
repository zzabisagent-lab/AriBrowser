"""Playwright 드라이버만 수집하고 Chromium의 원본 앱 번들은 빌드 스크립트에서 복사한다.

macOS의 Chromium .app/Framework 심볼릭 링크와 제공자 서명을 보존하기 위한 훅이다.
일반 Playwright 훅과 동일하게 드라이버 자료를 수집하되, 이전 로컬 빌드에서 남은
.local-browsers 디렉터리까지 PyInstaller 바이너리 분석에 들어가지 않도록 제외한다.
"""

from PyInstaller.utils.hooks import collect_data_files

datas = [entry for entry in collect_data_files("playwright") if ".local-browsers" not in entry[0]]

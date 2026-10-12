# Third-party components

AriBrowser 배포 파일에는 다음 오픈 소스 구성 요소와 그 종속 라이브러리가 포함됩니다. AriBrowser의 MIT 라이선스가 이 구성 요소의 라이선스를 대체하지 않습니다.

| Component | License | Source |
| --- | --- | --- |
| Playwright | Apache-2.0 | https://github.com/microsoft/playwright-python |
| Chromium / Chrome for Testing | BSD-style and bundled third-party licenses | https://chromium.googlesource.com/chromium/src/ |
| Python | PSF License | https://www.python.org/psf/license/ |
| Pillow | MIT-CMU | https://github.com/python-pillow/Pillow |
| psutil | BSD-3-Clause | https://github.com/giampaolo/psutil |
| greenlet | MIT / PSF | https://github.com/python-greenlet/greenlet |
| pyee | MIT | https://github.com/jfhbrook/pyee |
| typing-extensions | PSF | https://github.com/python/typing_extensions |
| Node.js (Playwright driver) | MIT and third-party notices | https://github.com/nodejs/node |
| PyInstaller bootloader | GPL with distribution exception | https://pyinstaller.org/en/stable/license.html |

Playwright/Chromium이 제공하는 LICENSE/NOTICE와 브라우저 배포 자료는 런타임 `_internal/playwright/driver` 안에 함께 포함됩니다. Python 및 설치된 Python 패키지 라이선스 사본은 배포의 `licenses/`에 보존됩니다. Chromium의 전체 오픈 소스 구성 요소 정보는 Chromium의 `chrome://credits`와 해당 소스 트리에서도 확인할 수 있습니다.

Tesseract OCR은 선택적으로 사용자가 설치하는 외부 프로그램이며 기본 바이너리에는 포함하지 않습니다. Tesseract는 Apache-2.0 라이선스입니다.

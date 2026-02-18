#!/usr/bin/env bash
# Install guide for Javis macOS menubar client (Swift)

echo "Javis macOS 클라이언트 설치 방법 (Swift):"
echo ""
echo "  1. Xcode에서 프로젝트 열기:"
echo "     open ios/JavisClient/JavisClient.xcodeproj"
echo ""
echo "  2. 'JavisClient-macOS' 타겟 선택 후 Cmd+B 빌드"
echo ""
echo "  3. 빌드된 .app을 ~/Applications/ 에 복사"
echo "     (DerivedData/JavisClient/Build/Products/Debug/JavisClient-macOS.app)"
echo ""
echo "  4. 시스템 설정 > 일반 > 로그인 항목 에 앱 추가 (자동 시작)"
echo ""
echo "Hub 서버 주소 변경:"
echo "  macOS/JavisMenuBarApp.swift 의 MacAppState init(hubURL:) 수정"

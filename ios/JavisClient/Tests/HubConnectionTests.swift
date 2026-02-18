import XCTest

final class HubConnectionTests: XCTestCase {
    func test_conversation_appends_final_and_ai() {
        let hub = HubConnection(hubURL: URL(string: "ws://localhost:8766")!)
        hub.handleIncomingText(#"{"type":"final","text":"파이썬이 뭐야?"}"#)
        hub.handleIncomingText(#"{"type":"ai","text":"파이썬은 프로그래밍 언어."}"#)
        XCTAssertEqual(hub.conversation.count, 2)
        XCTAssertEqual(hub.conversation[0].role, .user)
        XCTAssertEqual(hub.conversation[1].role, .ai)
    }

    func test_status_updates_on_status_message() {
        let hub = HubConnection(hubURL: URL(string: "ws://localhost:8766")!)
        hub.handleIncomingText(#"{"type":"status","value":"thinking"}"#)
        XCTAssertEqual(hub.status, .thinking)
    }

    func test_audio_callback_called_on_binary() {
        let hub = HubConnection(hubURL: URL(string: "ws://localhost:8766")!)
        var received: Data?
        hub.onAudioData = { received = $0 }
        hub.handleIncomingBinary(Data([0x01, 0x02]))
        XCTAssertEqual(received, Data([0x01, 0x02]))
    }
}

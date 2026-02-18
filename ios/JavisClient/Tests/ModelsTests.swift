import XCTest

final class ModelsTests: XCTestCase {
    func test_decode_status_message() throws {
        let json = #"{"type":"status","value":"connected"}"#
        let msg = try JSONDecoder().decode(HubMessage.self, from: Data(json.utf8))
        XCTAssertEqual(msg.type, "status")
        XCTAssertEqual(msg.value, "connected")
        XCTAssertNil(msg.text)
    }

    func test_decode_ai_message() throws {
        let json = #"{"type":"ai","text":"안녕하세요."}"#
        let msg = try JSONDecoder().decode(HubMessage.self, from: Data(json.utf8))
        XCTAssertEqual(msg.type, "ai")
        XCTAssertEqual(msg.text, "안녕하세요.")
    }

    func test_convmessage_prefix() {
        let u = ConvMessage(role: .user, text: "hi")
        let a = ConvMessage(role: .ai, text: "hello")
        XCTAssertEqual(u.prefix, "나")
        XCTAssertEqual(a.prefix, "자")
    }
}

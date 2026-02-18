import Foundation

enum SessionStatus: String, Equatable {
    case idle, connected, thinking, speaking, disconnected
}

struct HubMessage: Decodable {
    let type: String
    let value: String?
    let text: String?
}

struct ConvMessage: Identifiable, Equatable {
    enum Role { case user, ai }
    let id = UUID()
    let role: Role
    let text: String

    var prefix: String { role == .user ? "나" : "자" }
}

import AVFoundation
import Foundation

/// 마이크 캡처 및 TTS PCM 재생을 담당한다.
class AudioEngine: ObservableObject {
    private let engine = AVAudioEngine()
    private let inputNode: AVAudioInputNode
    private let playerNode = AVAudioPlayerNode()
    private let MIC_RATE: Double = 16000
    private let TTS_RATE: Double = 24000
    private let CHUNK_FRAMES = 1280  // 80ms @ 16kHz

    var onPCMChunk: ((Data) -> Void)?

    init() {
        inputNode = engine.inputNode
        engine.attach(playerNode)
        let ttsFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: TTS_RATE,
            channels: 1,
            interleaved: false
        )!
        engine.connect(playerNode, to: engine.mainMixerNode, format: ttsFormat)
    }

    func start() throws {
        try AVAudioSession.sharedInstance().setCategory(
            .playAndRecord,
            mode: .voiceChat,
            options: [.defaultToSpeaker, .allowBluetooth]
        )
        try AVAudioSession.sharedInstance().setActive(true)

        let micFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: MIC_RATE,
            channels: 1,
            interleaved: true
        )!

        inputNode.installTap(onBus: 0, bufferSize: AVAudioFrameCount(CHUNK_FRAMES),
                              format: micFormat) { [weak self] buffer, _ in
            guard let self = self,
                  let data = self.bufferToData(buffer) else { return }
            self.onPCMChunk?(data)
        }

        try engine.start()
        playerNode.play()
    }

    func stop() {
        inputNode.removeTap(onBus: 0)
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false)
    }

    func playPCMFloat32(_ data: Data) {
        let floatCount = data.count / 4
        guard floatCount > 0 else { return }
        let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: TTS_RATE,
            channels: 1,
            interleaved: false
        )!
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format,
                                             frameCapacity: AVAudioFrameCount(floatCount)) else { return }
        buffer.frameLength = AVAudioFrameCount(floatCount)
        data.withUnsafeBytes { ptr in
            let floats = ptr.bindMemory(to: Float.self)
            buffer.floatChannelData?[0].update(from: floats.baseAddress!, count: floatCount)
        }
        playerNode.scheduleBuffer(buffer, completionHandler: nil)
    }

    private func bufferToData(_ buffer: AVAudioPCMBuffer) -> Data? {
        guard let channelData = buffer.int16ChannelData else { return nil }
        let frameLength = Int(buffer.frameLength)
        return Data(bytes: channelData[0], count: frameLength * 2)
    }
}

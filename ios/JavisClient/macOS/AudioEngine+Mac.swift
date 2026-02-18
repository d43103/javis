import AVFoundation
import Combine

class AudioEngineMac: ObservableObject {
    @Published var inputLevel: Float = 0.0

    var onPCMChunk: ((Data) -> Void)?

    private let engine = AVAudioEngine()
    private let playerNode = AVAudioPlayerNode()
    private let MIC_RATE: Double = 16000
    private let TTS_RATE: Double = 24000
    private let CHUNK_FRAMES = 1280  // 80ms @ 16kHz

    init() {
        engine.attach(playerNode)
        let ttsFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: TTS_RATE, channels: 1, interleaved: false)!
        engine.connect(playerNode, to: engine.mainMixerNode, format: ttsFormat)
    }

    func start() throws {
        let micFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: MIC_RATE, channels: 1, interleaved: true)!

        engine.inputNode.installTap(
            onBus: 0,
            bufferSize: AVAudioFrameCount(CHUNK_FRAMES),
            format: micFormat
        ) { [weak self] buffer, _ in
            guard let self else { return }
            if let data = self.bufferToData(buffer) {
                self.onPCMChunk?(data)
            }
            let frames = Int(buffer.frameLength)
            if frames > 0, let ptr = buffer.int16ChannelData?[0] {
                let sum: Float = (0..<frames).reduce(0.0) { acc, i in
                    let s = Float(ptr[i]) / 32768.0
                    return acc + s * s
                }
                let rms = min(sqrt(sum / Float(frames)) * 5.0, 1.0)
                DispatchQueue.main.async { self.inputLevel = rms }
            }
        }

        try engine.start()
        playerNode.play()
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        DispatchQueue.main.async { self.inputLevel = 0.0 }
    }

    func playPCMFloat32(_ data: Data) {
        let floatCount = data.count / 4
        guard floatCount > 0 else { return }
        let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: TTS_RATE, channels: 1, interleaved: false)!
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format,
                                            frameCapacity: AVAudioFrameCount(floatCount)) else { return }
        buffer.frameLength = AVAudioFrameCount(floatCount)
        data.withUnsafeBytes { ptr in
            buffer.floatChannelData?[0].update(
                from: ptr.bindMemory(to: Float.self).baseAddress!,
                count: floatCount)
        }
        playerNode.scheduleBuffer(buffer)
    }

    private func bufferToData(_ buffer: AVAudioPCMBuffer) -> Data? {
        guard let ch = buffer.int16ChannelData else { return nil }
        return Data(bytes: ch[0], count: Int(buffer.frameLength) * 2)
    }
}

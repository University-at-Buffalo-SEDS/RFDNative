// Reproducible vector artwork for the local RFD Native app (no vendor logo).
import AppKit

let destination = CommandLine.arguments[1]
let size = 1024
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
let context = NSGraphicsContext(bitmapImageRep: bitmap)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = context

let tile = NSBezierPath(roundedRect: NSRect(x: 40, y: 40, width: 944, height: 944), xRadius: 210, yRadius: 210)
NSGradient(starting: NSColor(red: 0.12, green: 0.24, blue: 0.38, alpha: 1),
           ending: NSColor(red: 0.025, green: 0.07, blue: 0.14, alpha: 1))!.draw(in: tile, angle: -70)
NSColor(white: 1, alpha: 0.14).setStroke()
tile.lineWidth = 4
tile.stroke()

func stroke(_ points: [NSPoint], _ color: NSColor, _ width: CGFloat) {
    let path = NSBezierPath()
    path.move(to: points[0])
    for point in points.dropFirst() { path.line(to: point) }
    path.lineWidth = width
    path.lineCapStyle = .round
    path.lineJoinStyle = .round
    color.setStroke()
    path.stroke()
}
let signal = NSColor(red: 0.22, green: 0.88, blue: 0.94, alpha: 1)
let light = NSColor(red: 0.86, green: 0.96, blue: 1, alpha: 1)
// Antenna mast, foot, and cross braces.
stroke([NSPoint(x: 342,y: 240),NSPoint(x: 512,y: 620),NSPoint(x: 682,y: 240)], light, 34)
stroke([NSPoint(x: 311,y: 230),NSPoint(x: 713,y: 230)], light, 34)
stroke([NSPoint(x: 413,y: 400),NSPoint(x: 609,y: 400)], light, 22)
stroke([NSPoint(x: 440,y: 460),NSPoint(x: 602,y: 278)], light, 20)
stroke([NSPoint(x: 584,y: 460),NSPoint(x: 422,y: 278)], light, 20)
// Two matching outward wave pairs, centered on the transmitter.
for radius: CGFloat in [128, 235] {
    for range: (CGFloat, CGFloat) in [(135,225),(-45,45)] {
        let arc = NSBezierPath()
        arc.appendArc(withCenter: NSPoint(x: 512,y: 653), radius: radius,
                      startAngle: range.0, endAngle: range.1)
        arc.lineWidth = 31
        arc.lineCapStyle = .round
        signal.setStroke()
        arc.stroke()
    }
}
signal.setFill()
NSBezierPath(ovalIn: NSRect(x: 475,y: 616,width: 74,height: 74)).fill()
// Green activity indicator.
NSColor(red: 0.37,green: 0.95,blue: 0.62,alpha: 1).setFill()
NSBezierPath(ovalIn: NSRect(x: 770,y: 770,width: 62,height: 62)).fill()
NSGraphicsContext.restoreGraphicsState()
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: destination))

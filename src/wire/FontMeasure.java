package wire;

import javax.microedition.lcdui.Font;

/** Device-backed TextMeasure: bridges the pure Wrap engine to a real MIDP Font. */
public final class FontMeasure implements TextMeasure {
    private final Font font;
    public FontMeasure(Font f) { this.font = f; }
    public int charWidth(char c) { return font.charWidth(c); }
}

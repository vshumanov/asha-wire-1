package wire;

/** Character-width measure, so the wrap engine stays MIDP-free and testable. */
public interface TextMeasure {
    int charWidth(char c);
}

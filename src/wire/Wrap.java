package wire;

import java.util.Vector;

/**
 * Word-wrap a string into display lines for a given width. Hard '\n' breaks are
 * honoured (blank lines preserved). Pure and side-effect free -> desktop-tested.
 *
 * Same wrapping rules as the Reader's Paginator, but it lays out ALL lines of a
 * short in-memory item rather than one streamed page.
 */
public final class Wrap {
    private Wrap() {}

    public static String[] lines(String text, TextMeasure m, int maxWidth) {
        Vector out = new Vector();
        if (text == null) text = "";
        int n = text.length();
        int i = 0;
        while (i < n) {
            int lineStart = i;
            int w = 0;
            int lastSpace = -1;
            int j = i;
            int cut, resume;
            while (true) {
                if (j >= n) { cut = n; resume = n; break; }
                char c = text.charAt(j);
                if (c == '\n') { cut = j; resume = j + 1; break; }   // hard break, consume '\n'
                int cw = m.charWidth(c);
                if (w + cw > maxWidth && j > lineStart) {
                    if (lastSpace >= lineStart) { cut = lastSpace; resume = lastSpace + 1; }
                    else { cut = j; resume = j; }                     // split a too-long word
                    break;
                }
                w += cw;
                if (c == ' ') lastSpace = j;
                j++;
            }
            out.addElement(text.substring(lineStart, cut));
            i = resume;
            if (resume <= lineStart && resume < n) i = lineStart + 1; // never stall
        }
        if (out.size() == 0) out.addElement("");
        String[] arr = new String[out.size()];
        for (int k = 0; k < arr.length; k++) arr[k] = (String) out.elementAt(k);
        return arr;
    }
}

package wire;

import java.util.Vector;

/**
 * The day's baked wire: a date line plus up to MAX_ITEMS headlines. Parses the
 * WIRE1 line-format the home server produces. Pure and desktop-tested.
 *
 * Format (UTF-8):
 *   WIRE1                        <- magic (line 1)
 *   Thu 18 Sep 2026              <- date, shown as the index title (line 2)
 *   # [RETRO] A headline here    <- '#' starts an item; [TAG] is optional
 *   body line one                <- everything until the next '#' is the body
 *   body line two
 *   # [TECH] Next headline
 *   ...
 *
 * The 10-item cap is enforced HERE as well as on the server: the bound is the
 * point, so the phone will not render an 11th even if the server misbehaves.
 */
public final class Digest {
    public static final int MAX_ITEMS = 10;
    public static final String MAGIC = "WIRE1";

    private final String date;
    private final Item[] items;

    private Digest(String date, Item[] items) {
        this.date = date;
        this.items = items;
    }

    public String date() { return date; }
    public int count() { return items.length; }
    public Item item(int i) { return items[i]; }

    /** Parse baked bytes; null if they are not a valid WIRE1 digest. */
    public static Digest parse(byte[] bytes) {
        if (bytes == null) return null;
        return parse(Utf8.decode(bytes));
    }

    public static Digest parse(String all) {
        if (all == null) return null;
        String[] raw = splitLines(all);
        int p = 0;
        while (p < raw.length && raw[p].trim().length() == 0) p++;   // leading blanks
        if (p >= raw.length || !raw[p].trim().equals(MAGIC)) return null;
        p++;
        String date = (p < raw.length) ? raw[p].trim() : "";
        if (p < raw.length) p++;

        Vector items = new Vector();
        String tag = null, head = null;
        StringBuffer body = new StringBuffer();
        boolean inItem = false;

        for (; p < raw.length; p++) {
            String line = raw[p];
            if (line.length() > 0 && line.charAt(0) == '#') {
                if (inItem) { items.addElement(makeItem(tag, head, body)); inItem = false; }
                if (items.size() >= MAX_ITEMS) break;                 // never start an 11th
                String h = line.substring(1).trim();
                tag = "";
                if (h.startsWith("[")) {
                    int e = h.indexOf(']');
                    if (e > 0) { tag = h.substring(1, e).trim(); h = h.substring(e + 1).trim(); }
                }
                head = h;
                body = new StringBuffer();
                inItem = true;
            } else if (inItem) {
                if (body.length() > 0) body.append('\n');
                body.append(line);
            }
        }
        if (inItem && items.size() < MAX_ITEMS) items.addElement(makeItem(tag, head, body));

        Item[] arr = new Item[items.size()];
        for (int i = 0; i < arr.length; i++) arr[i] = (Item) items.elementAt(i);
        return new Digest(date, arr);
    }

    private static Item makeItem(String tag, String head, StringBuffer body) {
        String b = body.toString();
        int end = b.length();
        while (end > 0 && b.charAt(end - 1) == '\n') end--;           // drop trailing blanks
        return new Item(tag, head, b.substring(0, end));
    }

    private static String[] splitLines(String s) {
        Vector v = new Vector();
        int start = 0, n = s.length();
        for (int i = 0; i < n; i++) {
            if (s.charAt(i) == '\n') { v.addElement(stripCr(s.substring(start, i))); start = i + 1; }
        }
        v.addElement(stripCr(s.substring(start)));
        String[] a = new String[v.size()];
        for (int i = 0; i < a.length; i++) a[i] = (String) v.elementAt(i);
        return a;
    }

    private static String stripCr(String s) {
        if (s.length() > 0 && s.charAt(s.length() - 1) == '\r') return s.substring(0, s.length() - 1);
        return s;
    }
}

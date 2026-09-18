package wire;

/** One headline in the day's wire: an optional tag, a headline, and a short body. */
public final class Item {
    public final String tag;       // "" when none, e.g. "RETRO", "TECH", "CONCEPT"
    public final String headline;
    public final String body;

    public Item(String tag, String headline, String body) {
        this.tag = (tag == null) ? "" : tag;
        this.headline = (headline == null) ? "" : headline;
        this.body = (body == null) ? "" : body;
    }
}

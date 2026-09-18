package javax.microedition.rms;
// Compile-only stub. The phone supplies the real record store.
public class RecordStore {
  public static RecordStore openRecordStore(String name, boolean createIfNecessary)
      throws RecordStoreException { return null; }
  public static void deleteRecordStore(String name) throws RecordStoreException {}
  public void closeRecordStore() throws RecordStoreException {}
  public int getNumRecords() throws RecordStoreException { return 0; }
  public byte[] getRecord(int recordId) throws RecordStoreException { return null; }
  public int addRecord(byte[] data, int offset, int numBytes) throws RecordStoreException { return 0; }
  public void setRecord(int recordId, byte[] newData, int offset, int numBytes)
      throws RecordStoreException {}
  public void deleteRecord(int recordId) throws RecordStoreException {}
}

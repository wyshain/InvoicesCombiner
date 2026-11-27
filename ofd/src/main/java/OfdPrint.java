import java.nio.file.Path;
import org.ofdrw.converter.export.PDFExporter;
import org.ofdrw.reader.OFDReader;

public final class OfdPrint {
    public static void main(String[] args) throws Exception {
        try (OFDReader reader = new OFDReader(args[0], false);
             PDFExporter exporter = new PDFExporter(reader, Path.of(args[1]))) {
            exporter.export(0);
        }
    }
}

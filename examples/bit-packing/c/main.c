/* Builds the Status record described by bit_packing.xml, using the code
 * tcsxtce --bit-packing generated into bit_packing.h, and shows its packed
 * storage, a rejected value, and a serialize/deserialize round trip.
 *
 * The same file is built as C and as C++; both, and the Rust and Python
 * versions of this program, print identical output.
 */
#include <stdio.h>
#include <string.h>

#include "bit_packing.h"

static const char* hex(const uint8_t* b, size_t n, char* buf, size_t cap) {
  xtce_pk_text t = xtce_pk_text_init(buf, cap);
  xtce_pk_text_bytes(&t, b, n);
  return buf + 2; /* drop the "0x" */
}

static void print_binary(uint8_t b) {
  int bit;
  for (bit = 7; bit >= 0; --bit) putchar('0' + ((b >> bit) & 1));
}

int main(void) {
  bit_packing_Status status, back;
  xtce_pk_error err;
  uint8_t output[bit_packing_Status_MAX_BYTES];
  uint8_t input[bit_packing_Status_MAX_BYTES];
  char text[256], h[64];
  size_t i, len;

  /* Packed fields are set through setters, which check that each value
   * fits its encoding. */
  bit_packing_Status_init(&status);
  if (bit_packing_Status_set_mode(&status, bit_packing_ModeType_SCIENCE, &err) ||
      bit_packing_Status_set_level(&status, 19, &err) ||
      bit_packing_Status_set_offset(&status, -3, &err) ||
      bit_packing_Status_set_count(&status, 600, &err)) {
    return 1;
  }
  bit_packing_Status_set_enabled(&status, true);
  bit_packing_Status_describe(&status, text, sizeof text);
  printf("value: %s\n", text);

  /* The five fields share 3 bytes, in the order XTCE packs encoded data. */
  printf("storage: %u bytes\n", (unsigned)sizeof status.packed);
  for (i = 0; i < sizeof status.packed; ++i) {
    printf("storage byte %u: ", (unsigned)i);
    print_binary(status.packed[i]);
    putchar('\n');
  }

  /* A value too large for its 5 bits is rejected and nothing changes. */
  if (bit_packing_Status_set_level(&status, 32, &err)) {
    char msg[128];
    xtce_pk_error_message(&err, msg, sizeof msg);
    printf("set level 32 -> error:%s (%s); level is still %u\n", xtce_pk_error_kind(err.code), msg,
           (unsigned)bit_packing_Status_level(&status));
  } else {
    printf("set level 32 -> accepted\n");
  }

  /* The wire format is the XTCE one, most significant bit first. */
  if (bit_packing_Status_encode(&status, output, sizeof output, &len, &err)) return 1;
  printf("serialize -> %s\n", hex(output, len, h, sizeof h));
  memcpy(input, output, len);
  if (bit_packing_Status_decode(&back, input, len, &err)) return 1;
  bit_packing_Status_describe(&back, text, sizeof text);
  printf("deserialize -> %s\n", text);
  printf("storage after deserializing matches: %s\n",
         memcmp(back.packed, status.packed, sizeof status.packed) == 0 ? "true" : "false");
  return 0;
}

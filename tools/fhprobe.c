// Probe an LPC firmware hub: print ID responses and per-block CRCs. Never erases or programs.
#include <linux/module.h>
#include <linux/io.h>
#include <linux/crc32.h>
#include <linux/vmalloc.h>
MODULE_LICENSE("GPL");

static int __init fhprobe_init(void)
{
	volatile unsigned char __iomem *d = ioremap(0xFFF00000, 0x100000);
	volatile unsigned char __iomem *r = ioremap(0xFFB00000, 0x100000);
	unsigned char *buf;
	int i, b, ff, zero;

	if (!d || !r) { pr_info("fhprobe: ioremap failed\n"); goto out; }
	pr_info("fhprobe: regspace id (as fh's SST check) %02x %02x\n", r[0xC0000], r[0xC0001]);
	pr_info("fhprobe: array[0..15] %*ph\n", 16, (void *)d);
	pr_info("fhprobe: array[0xFFFF0..] %*ph\n", 16, (void *)(d + 0xFFFF0));

	d[0] = 0x90;	/* single-cycle Read ID (Intel/ST style), as fh does */
	pr_info("fhprobe: 0x90 id %02x %02x\n", d[0], d[1]);
	d[0] = 0xFF;
	d[0x5555] = 0xAA; d[0x2AAA] = 0x55; d[0x5555] = 0x90;	/* JEDEC software ID entry */
	pr_info("fhprobe: jedec id %02x %02x\n", d[0], d[1]);
	d[0] = 0xF0;	/* ID exit */
	d[0] = 0xFF;
	pr_info("fhprobe: array[0..15] after %*ph\n", 16, (void *)d);

	buf = vmalloc(0x10000);
	for (b = 0; buf && b < 16; b++) {
		for (i = 0; i < 0x10000; i++) buf[i] = d[b * 0x10000 + i];
		for (i = ff = zero = 0; i < 0x10000; i++) { ff += buf[i] == 0xFF; zero += buf[i] == 0; }
		pr_info("fhprobe: block %2d crc32 %08x ff %5d zero %5d\n", b, crc32_le(~0, buf, 0x10000) ^ ~0, ff, zero);
	}
	vfree(buf);
out:
	if (d) iounmap((void __iomem *)d);
	if (r) iounmap((void __iomem *)r);
	return -ENODEV;	/* never stays loaded */
}
module_init(fhprobe_init);

/*
 * of2_rp2.asl: the OpenFrame 2's mini-PCIe slot interrupt routing, which its firmware's ACPI
 * tables leave out. Installed as an SSDT by BlwPlatformDxe on an OpenFrame 2 only (acpi_rp2.inc).
 *
 * The slot sits behind the Poulsbo's second PCIe root port (00:1C.1, 8086:8112), whose own
 * interrupt pin is INTB. Every OF2 build's DSDT (Cisco, Telio, Avaya, OpenPeak: one table,
 * 16,548 bytes) describes only the first root port (P0P4, 00:1C.0) and routes only device 0x1C's
 * INTA (GSI 16, LNKA). With no routing for bus 2, an OS takes the slot's INTA through the
 * textbook bridge swizzle to 0x1C INTA, GSI 16, but the root port signals it on its own pin,
 * PIRQB, GSI 17. Seen on the Cisco OF2 (BCM43225, brcmsmac on IRQ 16): IRQ 17 "nobody cared",
 * disabled, and no Wi-Fi; with irqfixup the card works.
 *
 * The slot's INTA is PIRQB (proven), and B, C, D follow round, as on device 0x1D, whose
 * INTA-INTD are LNKA-LNKD, GSI 16-19 in this DSDT. Like the DSDT's own _PRT, this gives GSIs
 * in APIC mode (PICM set by _PIC) and the link devices otherwise.
 *
 * Built with iasl into of2_rp2.aml (tools/build_ucode_driver.py checks the two agree).
 */
DefinitionBlock ("", "SSDT", 2, "BLW", "OF2RP2", 0x00000001)
{
    External (\PICM, IntObj)
    External (\_SB.PCI0, DeviceObj)
    External (\_SB.LNKA, DeviceObj)
    External (\_SB.LNKB, DeviceObj)
    External (\_SB.LNKC, DeviceObj)
    External (\_SB.LNKD, DeviceObj)

    Scope (\_SB.PCI0)
    {
        Device (RP02)
        {
            Name (_ADR, 0x001C0001)
            Name (AR02, Package (0x04)
            {
                Package (0x04) { 0xFFFF, Zero, Zero, 0x11 },
                Package (0x04) { 0xFFFF, One, Zero, 0x12 },
                Package (0x04) { 0xFFFF, 0x02, Zero, 0x13 },
                Package (0x04) { 0xFFFF, 0x03, Zero, 0x10 }
            })
            Name (PR02, Package (0x04)
            {
                Package (0x04) { 0xFFFF, Zero, \_SB.LNKB, Zero },
                Package (0x04) { 0xFFFF, One, \_SB.LNKC, Zero },
                Package (0x04) { 0xFFFF, 0x02, \_SB.LNKD, Zero },
                Package (0x04) { 0xFFFF, 0x03, \_SB.LNKA, Zero }
            })
            Method (_PRT, 0, NotSerialized)
            {
                If (\PICM)
                {
                    Return (AR02)
                }
                Return (PR02)
            }
        }
    }
}

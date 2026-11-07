# Spustí originálny balík CrowdQCplus (m1-m5) nad CSV z qc.py a zapíše príznaky.
# Použitie: Rscript qc.R vstup.csv vystup.csv
#   vstup:  p_id,time(epoch s UTC),ta(°C),lon,lat,z(m)
#   vystup: p_id,time(epoch s UTC),ta,m1,m2,m3,m4,m5   (TRUE = prešla; príznaky sú kumulatívne)
# Riadky bez merania (ta prázdne) doplnil cqcp_padding; qc.py ich ignoruje.
# Parametre sú predvolené podľa balíka (m2 0,01/0,95; m3 0,2; m4 0,9; m5 3 km, 5 susedov, alfa 0,1).
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) stop("použitie: Rscript qc.R vstup.csv vystup.csv")

suppressMessages({
  library(data.table)
  library(CrowdQCplus)
})

d <- fread(args[1])
d[, time := as.POSIXct(time, origin = "1970-01-01", tz = "UTC")]
d <- cqcp_padding(d)
if (!isTRUE(cqcp_check_input(d))) stop("cqcp_check_input zlyhal (vstup nevyhovuje požiadavkám balíka)")

r <- cqcp_qcCWS(d, complete = TRUE, m5_keep_isolated = TRUE, includeOptional = FALSE, quiet = TRUE)
fwrite(r[, .(p_id, time = as.integer(time), ta, m1, m2, m3, m4, m5)], args[2])

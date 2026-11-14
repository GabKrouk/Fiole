# Generates reference values with base R (stats package) for the Fiole test suite.
# Run: Rscript make_reference.R > r_reference.json
out <- list()
num <- function(x) ifelse(is.infinite(x), ifelse(x > 0, "Infinity", "-Infinity"), sprintf("%.15g", x))
add <- function(name, vals) out[[name]] <<- vals

a <- c(0.7, -1.6, -0.2, -1.2, -0.1, 3.4, 3.7, 0.8, 0.0, 2.0)   # sleep, group 1
b <- c(1.9, 0.8, 1.1, 0.1, -0.1, 4.4, 5.5, 1.6, 4.6, 3.4)     # sleep, group 2
u <- c(12.1, 14.3, 9.8, 11.6, 13.9, 10.4, 15.2)               # no ties
v <- c(16.0, 13.1, 17.4, 18.9, 15.5, 14.8, 19.7, 16.6)
p1 <- c(5.1, 6.3, 4.8, 7.2, 5.9, 6.6, 5.4, 6.9)               # paired, no ties in |d|
p2 <- c(5.9, 6.4, 6.1, 7.0, 7.3, 7.9, 6.0, 8.5)

tt <- function(name, r) add(name, list(statistic=num(r$statistic), df=num(r$parameter), p=num(r$p.value),
                                        ci_low=num(r$conf.int[1]), ci_high=num(r$conf.int[2]), estimate=num(unname(r$estimate[1]) - ifelse(length(r$estimate)>1, unname(r$estimate[2]), 0))))
tt("welch_sleep", t.test(a, b))
tt("student_sleep", t.test(a, b, var.equal=TRUE))
tt("paired_sleep", t.test(a, b, paired=TRUE))
tt("welch_uv_less", t.test(u, v, alternative="less"))
tt("student_uv_greater_90", t.test(u, v, var.equal=TRUE, alternative="greater", conf.level=0.9))

wt <- function(name, r) add(name, list(statistic=num(r$statistic), p=num(r$p.value)))
suppressWarnings(wt("mw_sleep_ties", wilcox.test(a, b)))
wt("mw_uv_exact", wilcox.test(u, v))
wt("mw_uv_exact_greater", wilcox.test(u, v, alternative="greater"))
suppressWarnings(wt("wilcoxon_sleep_ties", wilcox.test(a, b, paired=TRUE)))
wt("wilcoxon_p_exact", wilcox.test(p1, p2, paired=TRUE))

# Cohen's d CI by inverting the noncentral t (base R only)
dci <- function(t, df, f, lvl=0.95) {
  lo <- uniroot(function(n) pt(t, df, n) - (1-(1-lvl)/2), c(t-30, t+30), tol=1e-12)$root
  hi <- uniroot(function(n) pt(t, df, n) - ((1-lvl)/2), c(t-30, t+30), tol=1e-12)$root
  c(lo*f, hi*f)
}
n1 <- length(a); n2 <- length(b)
sp <- sqrt(((n1-1)*var(a) + (n2-1)*var(b))/(n1+n2-2)); d <- (mean(a)-mean(b))/sp
ci <- dci(d/sqrt(1/n1+1/n2), n1+n2-2, sqrt(1/n1+1/n2))
add("cohen_d_sleep", list(value=num(d), ci_low=num(ci[1]), ci_high=num(ci[2])))
dd <- a-b; dz <- mean(dd)/sd(dd); ci <- dci(dz*sqrt(10), 9, 1/sqrt(10))
add("cohen_dz_sleep", list(value=num(dz), ci_low=num(ci[1]), ci_high=num(ci[2])))

# descriptives
add("desc_a", list(mean=num(mean(a)), sd=num(sd(a)), median=num(median(a)), q1=num(quantile(a, .25)), q3=num(quantile(a, .75)),
                   ci_low=num(t.test(a)$conf.int[1]), ci_high=num(t.test(a)$conf.int[2])))

# correlation / regression
x <- c(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
y <- c(2.3, 2.9, 4.1, 4.8, 6.2, 6.1, 7.9, 8.4, 9.0, 10.8, 11.1, 12.9)
r <- cor.test(x, y)
add("pearson", list(r=num(r$estimate), t=num(r$statistic), df=num(r$parameter), p=num(r$p.value), ci_low=num(r$conf.int[1]), ci_high=num(r$conf.int[2])))
y2 <- c(3.1, 1.2, 4.4, 2.0, 6.5, 5.1, 3.3, 8.8, 7.0, 9.9, 6.1, 12.0)
r <- cor.test(x, y2, method="spearman", exact=FALSE)
add("spearman", list(rho=num(r$estimate), p=num(r$p.value)))
fit <- lm(y ~ x); s <- summary(fit); cf <- coef(s); ci <- confint(fit)
add("lm", list(intercept=num(cf[1,1]), slope=num(cf[2,1]), se_int=num(cf[1,2]), se_slope=num(cf[2,2]),
               t_slope=num(cf[2,3]), p_slope=num(cf[2,4]), p_int=num(cf[1,4]), r2=num(s$r.squared), adj_r2=num(s$adj.r.squared),
               sigma=num(s$sigma), f=num(s$fstatistic[1]),
               slope_ci_low=num(ci[2,1]), slope_ci_high=num(ci[2,2]), int_ci_low=num(ci[1,1]), int_ci_high=num(ci[1,2])))
sh <- shapiro.test(a)
add("shapiro_a", list(W=num(sh$statistic), p=num(sh$p.value)))
# tiny p-value case
big1 <- seq(1, 100); big2 <- seq(1, 100) + 60
r <- t.test(big1, big2, var.equal=TRUE)
add("tiny_p", list(p=num(r$p.value)))

# write JSON by hand (no jsonlite needed)
cat("{\n")
nm <- names(out)
for (i in seq_along(nm)) {
  e <- out[[nm[i]]]
  fields <- paste0('"', names(e), '": ', unlist(e), collapse=", ")
  cat(sprintf('  "%s": {%s}%s\n', nm[i], fields, ifelse(i < length(nm), ",", "")))
}
cat("}\n")

<?php
/**
 * Run: php test-gateway.php /path/to/wordpress   (a throwaway install!)
 */
$wordpress_dir = $argv[1];
$_SERVER['HTTP_HOST'] = 'localhost';
$_SERVER['REQUEST_URI'] = '/';
require $wordpress_dir . '/wp-load.php';
require __DIR__ . '/wc-stubs.php';
require __DIR__ . '/../inventree-shop/inventree-shop.php';
do_action( 'plugins_loaded' );

$fails = 0;
function check( $label, $cond ) {
	global $fails;
	echo ( $cond ? 'ok   ' : 'FAIL ' ) . $label . "\n";
	if ( ! $cond ) { $fails++; }
}

$gateways = apply_filters( 'woocommerce_payment_gateways', array() );
check( 'gateway registered', in_array( 'InvenTree_Account_Gateway', $gateways, true ) );

function make_user( $login ) {
	$id = username_exists( $login );
	return $id ? $id : wp_create_user( $login, 'pw', $login . '@example.com' );
}
$plain    = make_user( 'plain' );
$approved = make_user( 'trade' );
$limited  = make_user( 'limited' );
// What the InvenTree plugin writes through the REST API:
update_user_meta( $approved, INVENTREE_META_APPROVED, 'yes' );
update_user_meta( $approved, INVENTREE_META_CREDIT, '' );
update_user_meta( $approved, INVENTREE_META_COMPANY, 'Pit Mining Pty Ltd' );
update_user_meta( $limited, INVENTREE_META_APPROVED, 'yes' );
update_user_meta( $limited, INVENTREE_META_CREDIT, '500.00' );
update_user_meta( $limited, INVENTREE_META_COMPANY, 'Small Co' );

delete_option( 'woocommerce_inventree_account_settings' ); // start from the defaults on every run
$g = new InvenTree_Account_Gateway();
WC()->cart->total = 450;

wp_set_current_user( 0 );
check( 'hidden when logged out', ! $g->is_available() );
wp_set_current_user( $plain );
check( 'hidden for a customer without an account', ! $g->is_available() );
wp_set_current_user( $approved );
check( 'shown for approved account, no limit', $g->is_available() );
wp_set_current_user( $limited );
check( 'shown when order fits credit (450 <= 500)', $g->is_available() );
WC()->cart->total = 500.004;
check( 'shown at the limit (rounding)', $g->is_available() );
WC()->cart->total = 650;
check( 'hidden when order is over credit (650 > 500)', ! $g->is_available() );
update_user_meta( $limited, INVENTREE_META_APPROVED, 'no' );
WC()->cart->total = 10;
check( 'hidden once InvenTree withdraws approval', ! $g->is_available() );

ob_start();
wp_set_current_user( $approved );
$g->payment_fields();
$html = ob_get_clean();
check( 'payment fields show the company and a PO box', false !== strpos( $html, 'Pit Mining Pty Ltd' ) && false !== strpos( $html, 'inventree_po_number' ) );

// --- process_payment ---
$GLOBALS['fake_orders'][101] = new Fake_Order( 101, $approved, 900 );
$_POST['inventree_po_number'] = "  PO-7781 <b>x</b>  ";
$r = $g->process_payment( 101 );
$o = wc_get_order( 101 );
check( 'approved order succeeds', 'success' === $r['result'] );
check( 'status set to processing', 'processing' === $o->status );
check( 'PO saved, sanitised', 'PO-7781 x' === $o->get_meta( INVENTREE_META_PO ) );
check( 'company recorded on order', 'Pit Mining Pty Ltd' === $o->get_meta( 'inventree_account_company' ) );
check( 'cart emptied', WC()->cart->emptied );

$GLOBALS['fake_orders'][102] = new Fake_Order( 102, $approved, 50 );
wp_set_current_user( $plain );
$r = $g->process_payment( 102 );
check( 'refused when the order belongs to someone else / no account', 'failure' === $r['result'] && 'pending' === wc_get_order( 102 )->status );

update_user_meta( $limited, INVENTREE_META_APPROVED, 'yes' );
$GLOBALS['fake_orders'][103] = new Fake_Order( 103, $limited, 800 );
wp_set_current_user( $limited );
$r = $g->process_payment( 103 );
check( 'refused when over credit at payment time', 'failure' === $r['result'] );

update_option( 'woocommerce_inventree_account_settings', array( 'enabled' => 'yes', 'po_field' => 'required', 'order_status' => 'on-hold' ) );
$g2 = new InvenTree_Account_Gateway();
$GLOBALS['fake_orders'][104] = new Fake_Order( 104, $limited, 100 );
$_POST['inventree_po_number'] = '   ';
$r = $g2->process_payment( 104 );
check( 'required PO enforced', 'failure' === $r['result'] );
$_POST['inventree_po_number'] = 'PO-1';
$r = $g2->process_payment( 104 );
check( 'on-hold status option honoured', 'success' === $r['result'] && 'on-hold' === wc_get_order( 104 )->status );

echo $fails ? "\n$fails FAILED\n" : "\nALL GATEWAY CHECKS PASSED\n";
exit( $fails ? 1 : 0 );
